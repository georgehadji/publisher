"""The Temporal workflow + activity that make R1's per-capability dispatch
real (docs/ARCHITECTURE_ROADMAP.md). See `queues.py` for why the capability
taxonomy this dispatches by (`decl.queue`) needed no new design -- it was
already declared on every stage, unread until this module.

Kept deliberately free of any `publisher_stages`/`publisher_exec`/`publisher_
cache` import AT MODULE TOP LEVEL: this file defines `BuildWorkflow`, and
Temporal's default sandboxed workflow runner re-executes whatever a
workflow-defining module imports at load time, on every worker start, under
restrictions meant to catch non-determinism (real time, threads, unvetted
I/O). None of those packages need to run inside that sandbox -- only
`run_stage_activity` (a plain activity, NOT sandboxed -- activities may do
real I/O) needs them, so it imports them lazily, inside its own function
body, exactly where `worker.py` already does `import stages` for the same
"registration side effect" reason.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

ORCHESTRATOR_TASK_QUEUE = "q.orchestrator"


@dataclass
class StageActivityInput:
    stage_name: str
    build_id: str
    explicit_inputs: dict[str, Any]
    artifact_paths: dict[str, str]
    cas_root: str
    database_url: str
    allow_stub_engines: bool = False


@dataclass
class StageActivityOutput:
    new_artifact_paths: dict[str, str]
    cache_hit: bool
    metrics: dict[str, float]
    artifacts: list[dict[str, Any]]


@activity.defn
async def run_stage_activity(payload: StageActivityInput) -> StageActivityOutput:
    """Runs exactly one stage -- what a `BuildWorkflow` step becomes on the
    wire. Registered only on the task queue(s) `worker_temporal.py` was
    started to host; Temporal's own queue routing is what makes a
    q.composition stage never land on a process polling only q.prepress --
    this function does not choose its queue, `BuildWorkflow.run` does (from
    `decl.queue`, via `workflow.execute_activity(..., task_queue=...)`).

    Plain, non-sandboxed activity code -- unlike the workflow, this may (and
    does) perform real I/O: opening the CAS, hitting Postgres for the cache
    index, running the actual stage function.
    """
    import stages  # noqa: F401 -- registration side effect, same as worker.py
    from publisher_cache import PostgresCacheStore
    from publisher_exec import run_single_stage
    from publisher_stages import get_registry

    registry = get_registry()
    decl = registry.get(payload.stage_name)
    if decl is None:
        raise ValueError(f"activity asked to run unknown stage '{payload.stage_name}'")

    resolved: dict[str, Any] = dict(payload.explicit_inputs)
    for param_name, schema_id in decl.inputs.items():
        if param_name in resolved:
            continue
        if schema_id in payload.artifact_paths:
            resolved[param_name] = payload.artifact_paths[schema_id]

    cache_store = PostgresCacheStore(payload.database_url)
    result, new_paths = run_single_stage(
        payload.stage_name, registry,
        build_id=payload.build_id,
        resolved_inputs=resolved,
        cas_root=payload.cas_root,
        cache_store=cache_store,
        allow_stub_engines=payload.allow_stub_engines,
    )
    return StageActivityOutput(
        new_artifact_paths=new_paths,
        cache_hit=result.cache_hit,
        metrics=dict(result.metrics),
        artifacts=[
            {"kind": a.kind, "hash": a.hash, "media_type": a.media_type, "size": a.size}
            for a in result.artifacts
        ],
    )


@dataclass
class BuildWorkflowInput:
    build_id: str
    order: list[str]
    initial_inputs: dict[str, dict[str, Any]]
    cas_root: str
    database_url: str
    # decl.queue for every stage in `order`, computed by the caller
    # (worker_temporal.py, from the same FrozenRegistry it built the plan
    # from) rather than re-derived in here -- see this module's docstring on
    # why workflow code stays out of the publisher_stages import graph.
    queue_by_stage: dict[str, str] = field(default_factory=dict)
    stage_timeout_s: int = 900


@workflow.defn
class BuildWorkflow:
    """The R1 replacement for `publisher_exec.run()`'s in-process loop: same
    ordered plan, same artifact-path threading between stages, but each
    stage dispatched as an activity on ITS OWN declared queue instead of
    called directly in this one process.

    Deterministic by construction: every decision this loop makes (which
    stage is next, what its inputs are) comes from `order` (fixed at
    workflow start) and `execute_activity` results (recorded in Temporal's
    event history and replayed identically on any retry/rehydration) --
    never a clock read, a random draw, or a registry re-lookup that could
    disagree between the original run and a later replay.
    """

    @workflow.run
    async def run(self, input: BuildWorkflowInput) -> dict[str, StageActivityOutput]:
        artifact_paths: dict[str, str] = {}
        results: dict[str, StageActivityOutput] = {}
        for stage_name in input.order:
            payload = StageActivityInput(
                stage_name=stage_name,
                build_id=input.build_id,
                explicit_inputs=input.initial_inputs.get(stage_name, {}),
                artifact_paths=dict(artifact_paths),
                cas_root=input.cas_root,
                database_url=input.database_url,
            )
            queue = input.queue_by_stage.get(stage_name, "q.default")
            output: StageActivityOutput = await workflow.execute_activity(
                run_stage_activity,
                payload,
                task_queue=queue,
                start_to_close_timeout=timedelta(seconds=input.stage_timeout_s),
            )
            results[stage_name] = output
            artifact_paths.update(output.new_artifact_paths)
        return results


def build_worker(client: Client, task_queue: str, *, host_workflow: bool) -> Worker:
    """One Temporal `Worker` polling exactly ONE task queue. `worker_temporal.py`
    starts one of these per queue it was configured to host (`PUBLISHER_
    TEMPORAL_QUEUES`) -- this IS the per-capability pool: point one set of
    replicas at `task_queue="q.composition"` and another at
    `task_queue="q.prepress"`, and each scales independently of the other,
    which `worker.py`'s single-process-runs-every-stage model cannot do.

    `host_workflow=True` also registers `BuildWorkflow` on this worker --
    exactly one queue needs it (`ORCHESTRATOR_TASK_QUEUE`); every stage
    queue hosts activities only, never the workflow.
    """
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[BuildWorkflow] if host_workflow else [],
        activities=[run_stage_activity],
    )


async def run_workers(
    address: str, namespace: str, task_queues: list[str], *, host_orchestrator: bool
) -> None:
    """Connects once, starts one `Worker` per queue in `task_queues` (plus
    `ORCHESTRATOR_TASK_QUEUE` if `host_orchestrator`), and runs them
    concurrently until cancelled. `worker_temporal.py` calls this from its
    own `asyncio.run()`."""
    client = await Client.connect(address, namespace=namespace)
    queues = list(dict.fromkeys(task_queues))  # de-dup, preserve order
    if host_orchestrator and ORCHESTRATOR_TASK_QUEUE not in queues:
        queues.append(ORCHESTRATOR_TASK_QUEUE)
    workers = [
        build_worker(client, q, host_workflow=(q == ORCHESTRATOR_TASK_QUEUE))
        for q in queues
    ]
    if not workers:
        return
    await asyncio.gather(*(w.run() for w in workers))


__all__ = [
    "ORCHESTRATOR_TASK_QUEUE",
    "StageActivityInput",
    "StageActivityOutput",
    "run_stage_activity",
    "BuildWorkflowInput",
    "BuildWorkflow",
    "build_worker",
    "run_workers",
]
