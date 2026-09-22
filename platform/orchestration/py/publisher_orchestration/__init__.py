"""
publisher_orchestration -- optional Temporal-backed per-capability dispatch
(R1, docs/ARCHITECTURE_ROADMAP.md).

WHAT THIS WIRES, AND WHY IT WAS SAFE TO ADD
Every stage in stages/*.py already declares `queue="q.xxx"` in its
`@stage(...)` call (`StageDeclaration.queue`, `platform/stages/py/
publisher_stages/__init__.py`) -- a real 7-way taxonomy (q.ingest,
q.structure, q.composition, q.render.html, q.prepress, q.external,
q.default) that nothing in the codebase had ever read before this package.
Same shape as the epub/onix/alttext finding E7.2 wired: fully declared, zero
callers. `queues.discover_queues()` is what reads it.

WHAT STAYS UNTOUCHED
`worker.py` and `publisher_exec.run()` do not import this package and do not
know it exists. The default `docker compose up -d` path -- one process,
Postgres `FOR UPDATE SKIP LOCKED`, `publisher_exec.run()` executing the
whole plan in-process -- is unchanged in every respect. This is a second,
independent production tier (`worker_temporal.py`, repo root), opt-in via
`docker compose --profile temporal up` (see docker-compose.yml).

WHAT THIS BUYS THAT worker.py CANNOT
`worker.py` runs an entire build's stage DAG in ONE process -- design-compile
and finish-gs and cover-art all compete for the same CPU/memory/network in
the same container, so "more render capacity" means scaling everything.
Here, `temporal_runtime.BuildWorkflow` dispatches each stage as a Temporal
*activity* addressed to `decl.queue` -- a separate `worker_temporal.py`
process, polling only that one task queue, executes it. Run more replicas of
the q.composition pool (weasyprint/Typst, CPU-heavy) without touching the
q.prepress pool (Ghostscript, comparatively cheap) -- the "per-capability
autoscaling" ARCHITECTURE_ROADMAP.md R1 names as genuinely missing.

WHAT THIS DOES NOT BUY (still genuinely missing, still R1's open half)
No autoscaling POLICY -- nothing here decides how many replicas of a pool to
run; that is still an operator decision, same as scaling `worker.py`
replicas today. No workflow-versioning story for an in-flight build across
a stage-logic change (Temporal supports this via `workflow.patched()`, but
adopting it is deferred until a real in-flight migration needs it, per the
roadmap's own "logged, not scheduled" framing).

Two submodules, split deliberately:
  queues.py           -- `discover_queues()`. Imports `publisher_stages`
                          freely; never imported by a workflow-defining file.
  temporal_runtime.py -- `BuildWorkflow`, `run_stage_activity`, `run_workers`.
                          Free of any `publisher_stages` import at module
                          top level -- see its own docstring for why.
"""

from .queues import discover_queues
from .temporal_runtime import (
    ORCHESTRATOR_TASK_QUEUE,
    BuildWorkflow,
    BuildWorkflowInput,
    StageActivityInput,
    StageActivityOutput,
    build_worker,
    run_stage_activity,
    run_workers,
)

__all__ = [
    "discover_queues",
    "ORCHESTRATOR_TASK_QUEUE",
    "BuildWorkflow",
    "BuildWorkflowInput",
    "StageActivityInput",
    "StageActivityOutput",
    "build_worker",
    "run_stage_activity",
    "run_workers",
]
