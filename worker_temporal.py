#!/usr/bin/env python3
"""
R1 orchestration path -- Temporal-backed alternative to worker.py's
in-process DagExecutor.run(), for docs/ARCHITECTURE_ROADMAP.md R1's stated
gap: per-capability worker pools that scale independently.

ADDITIVE, OPT-IN, FLAG-GATED. Nothing here changes worker.py's behavior, and
worker.py does not import this module. The default `docker compose up -d`
still starts the Postgres/in-process path unchanged. This path starts only
via `docker compose --profile temporal up -d` (docker-compose.yml).

Reuses worker.py's Postgres claim/lease/record functions directly (imported,
not duplicated) so the two paths cannot drift on lease semantics, error
recording, or the terminal-`package`-stage hard-gate check. What differs is
ONLY how a claimed build's stages are executed: instead of one process
running `publisher_exec.run()` in-order in-place, `_run_build_via_temporal`
starts a `BuildWorkflow` that dispatches each stage as an activity on that
stage's OWN capability queue (`decl.queue` -- see `platform/orchestration`'s
own docstring for the finding that this queue taxonomy already existed,
declared on every stage, unread until now), so a separate process --
potentially a separate host -- can host just `q.composition`'s workers and
scale independently of `q.prepress`'s.

PUBLISHER_TEMPORAL_ROLE selects what this process does:
  all (default)  -- runs the claim loop AND hosts every discovered queue's
                     activities AND the orchestrator queue. One container
                     that completes a whole build alone -- the
                     lowest-friction way to try this path.
  orchestrator   -- runs the claim loop and hosts q.orchestrator, plus
                     whatever PUBLISHER_TEMPORAL_QUEUES additionally lists
                     (default: none -- a pure dispatcher).
  worker         -- hosts ONLY the queues in PUBLISHER_TEMPORAL_QUEUES
                     (required, comma-separated). No claim loop, no
                     workflow. THIS is the per-capability pool: run N of
                     these with different queue sets, scaled independently.

KNOWN, DOCUMENTED FIRST-CUT GAPS (not hidden):
- Per-stage build_stages/artifacts rows are recorded in ONE BATCH when the
  workflow finishes, not incrementally as each stage completes -- GET
  /v1/builds/:id/events (SSE) will not show live per-stage progress for a
  Temporal-path build the way it does for the Postgres path.
- A Temporal-path failure is always recorded as error_kind='INTERNAL',
  never reclassified back to the original StageError.kind (BAD_INPUT,
  TIMEOUT, ...) -- that needs a custom Temporal failure converter to round-
  trip StageError's fields through activity failure serialization, which is
  a real follow-up, not required to prove per-capability dispatch works.
Neither gap affects correctness of the build itself or of queue routing --
both are about how much detail this path surfaces back to Postgres/the API
while the build (or its failure) is in flight.

WHAT IS NOT A GAP: build lease renewal. `_run_build_via_temporal` polls the
workflow handle with a bounded timeout in a loop specifically so it can call
`worker._renew_lease` periodically WHILE a long build is still running --
without this, a build that runs longer than PUBLISHER_WORKER_LEASE_SECONDS
(600s default) would have its lease expire mid-flight and get reclaimed by
another worker (Temporal-path or Postgres-path) while the original workflow
was still genuinely in progress, which is exactly the double-processing
`FOR UPDATE SKIP LOCKED` + leasing exists to prevent.
"""

from __future__ import annotations

import asyncio
import os
import random
import signal
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

REPO_ROOT = Path(__file__).resolve().parent
for sub in ("platform/stages/py", "platform/cas/py", "platform/cache/py",
            "platform/exec/py", "platform/orchestration/py"):
    sys.path.insert(0, str(REPO_ROOT / sub))

import stages  # noqa: F401 -- registration side effect
import worker  # reuse claim/lease/record/fail helpers -- see module docstring
from publisher_exec import plan
from publisher_stages import StageError, build_registry
from publisher_orchestration import discover_queues, ORCHESTRATOR_TASK_QUEUE
from publisher_orchestration.temporal_runtime import BuildWorkflow, BuildWorkflowInput, run_workers
from temporalio.client import Client

# Same env-parsed flag worker.py's own module scope reads -- both entry
# points must agree on whether idml is registered, since they may run
# against the SAME registry-derived queue set in a mixed deployment.
stages.import_idml_if_requested(
    os.environ.get("PUBLISHER_EMIT_IDML", "").strip().lower() in ("1", "true", "yes")
)

TEMPORAL_ADDRESS = os.environ.get("PUBLISHER_TEMPORAL_ADDRESS", "temporal:7233")
TEMPORAL_NAMESPACE = os.environ.get("PUBLISHER_TEMPORAL_NAMESPACE", "default")
ROLE = os.environ.get("PUBLISHER_TEMPORAL_ROLE", "all").strip().lower()
_QUEUES_ENV = os.environ.get("PUBLISHER_TEMPORAL_QUEUES", "").strip()

# How often to poll the in-flight workflow's handle so a long build renews
# its Postgres lease -- comfortably under PUBLISHER_WORKER_LEASE_SECONDS
# (600s default; worker.py), the same margin worker.py's own per-stage
# renewal implies for a build with several-second stages.
_LEASE_RENEW_INTERVAL_S = 60


def _queues_from_env() -> list[str]:
    return [q.strip() for q in _QUEUES_ENV.split(",") if q.strip()]


async def _run_build_via_temporal(conn, build: dict, client: Client) -> None:
    build_id = build["id"]
    tenant_id = build["tenant_id"]
    worker._CURRENT_BUILD.clear()
    worker._CURRENT_BUILD["build_id"] = build_id
    if build.get("correlation_id"):
        worker._CURRENT_BUILD["correlation_id"] = build["correlation_id"]

    registry = build_registry(worker._registry_config_from_env())
    try:
        initial_inputs = worker._initial_inputs_for(conn, build, registry)
    except StageError as e:
        worker._fail(conn, build_id, e.kind.value, e.message)
        return

    execution_plan = plan(registry, initial_inputs)
    # Same hard-gate check worker.run_build makes post-hoc -- doing it before
    # ever starting a workflow is strictly cheaper and just as correct.
    if "package" not in execution_plan.order:
        worker._fail(
            conn, build_id, "ENGINE_BUG",
            f"terminal stage 'package' did not appear in the execution plan "
            f"({len(execution_plan.order)} stages planned) -- refusing to "
            "run without a preflight verdict and package report",
        )
        return

    queue_by_stage = {name: registry.get(name).queue for name in execution_plan.order}
    wf_input = BuildWorkflowInput(
        build_id=build_id,
        order=list(execution_plan.order),
        initial_inputs=initial_inputs,
        cas_root=str(worker.CAS_ROOT),
        database_url=worker._dsn(),
        queue_by_stage=queue_by_stage,
    )

    handle = await client.start_workflow(
        BuildWorkflow.run, wf_input,
        id=f"build-{build_id}", task_queue=ORCHESTRATOR_TASK_QUEUE,
    )

    try:
        while True:
            try:
                results = await asyncio.wait_for(
                    asyncio.shield(handle.result()), timeout=_LEASE_RENEW_INTERVAL_S,
                )
                break
            except asyncio.TimeoutError:
                worker._renew_lease(conn, build_id)
    except Exception as e:
        # Known gap (module docstring): every Temporal-path failure records
        # as INTERNAL, not reclassified back to the original StageError.kind.
        worker._fail(conn, build_id, "INTERNAL", f"{type(e).__name__}: {e}")
        worker._log_info("build failed via temporal workflow", error=str(e))
        return

    # Known gap (module docstring): recorded in one batch here, not
    # incrementally as each stage finished -- no live SSE progress yet.
    for stage_name in execution_plan.order:
        output = results[stage_name]
        decl = registry.get(stage_name)
        worker._record_stage(
            conn, build_id, tenant_id, stage_name, decl.version,
            "completed", output.cache_hit, 0, output.metrics,
        )
        for art in output.artifacts:
            worker._record_artifact(
                conn, build_id, tenant_id, art["kind"],
                decl.outputs.get(art["kind"], ""), art["hash"],
                art["media_type"], art["size"],
            )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE builds SET status = 'completed', completed_at = now() WHERE id = %s",
            (build_id,),
        )
        worker._notify(conn, build_id, {"status": "completed"})
        conn.commit()
    worker._log_info("build completed via temporal", stages=len(results))


async def _claim_loop(client: Client) -> int:
    """Same shape as worker.py's `main()` poll loop, reusing its claim/fail
    helpers directly -- only the "how a claimed build actually runs" step
    differs (`_run_build_via_temporal` instead of `worker.run_build`)."""
    dsn = worker._dsn()
    while True:
        if worker._stop_requested:
            worker._log_info("SIGTERM received -- exiting cleanly, no new claims")
            return 0
        try:
            conn = psycopg2.connect(dsn)
        except psycopg2.OperationalError as e:
            worker._log_info("database unavailable, retrying", error=str(e))
            await asyncio.sleep(worker.POLL_INTERVAL_S)
            continue
        try:
            build = worker._claim_build(conn)
            if build is not None:
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (build["tenant_id"],))
                conn.commit()
                await _run_build_via_temporal(conn, build, client)
                worker._CURRENT_BUILD.clear()
                continue
        except psycopg2.OperationalError as e:
            worker._log_info("lost the database mid-poll, reconnecting", error=str(e))
        finally:
            conn.close()
        if worker._stop_requested:
            worker._log_info("SIGTERM received after build -- exiting cleanly")
            return 0
        await asyncio.sleep(worker.POLL_INTERVAL_S * (0.5 + random.random()))


async def main() -> int:
    signal.signal(signal.SIGTERM, worker._handle_sigterm)
    worker._log_info(
        "starting worker_temporal", role=ROLE,
        address=TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE,
    )

    if ROLE == "worker":
        queues = _queues_from_env()
        if not queues:
            raise RuntimeError(
                "PUBLISHER_TEMPORAL_ROLE=worker requires PUBLISHER_TEMPORAL_QUEUES "
                "(comma-separated, e.g. 'q.composition,q.render.html')"
            )
        await run_workers(TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, queues, host_orchestrator=False)
        return 0

    if ROLE not in ("all", "orchestrator"):
        raise RuntimeError(
            f"PUBLISHER_TEMPORAL_ROLE={ROLE!r} is not one of all|orchestrator|worker"
        )

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    if ROLE == "all":
        registry = build_registry(worker._registry_config_from_env())
        host_queues = list(discover_queues(registry))
    else:
        host_queues = _queues_from_env()

    worker_task = asyncio.create_task(
        run_workers(TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, host_queues, host_orchestrator=True)
    )
    claim_task = asyncio.create_task(_claim_loop(client))
    done, pending = await asyncio.wait({worker_task, claim_task}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in done:
        exc = t.exception()
        if exc is not None:
            raise exc
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
