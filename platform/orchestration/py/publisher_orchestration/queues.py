"""Capability-queue discovery -- plain data, no Temporal import.

Deliberately its own module, separate from `temporal_runtime.py` (which
defines the actual `@workflow.defn`/`@activity.defn`): Temporal's workflow
sandbox re-executes whatever a workflow-defining module imports at load
time, and this function is only ever called by `worker_temporal.py` at
plain process startup, never from inside workflow code. Keeping it out of
the workflow file means it can import `publisher_stages` freely without
that import ever being subject to sandbox restrictions it does not need.
"""

from __future__ import annotations

from publisher_stages import StageRegistry


def discover_queues(registry: StageRegistry) -> tuple[str, ...]:
    """Every distinct `decl.queue` a registry's stages declare, sorted.

    Derived from the registry, never hand-listed. Every stage in
    `stages/*.py` already declares `queue="q.xxx"` in its `@stage(...)` call
    (R1, docs/ARCHITECTURE_ROADMAP.md finding: a fully-authored 7-way
    taxonomy -- q.ingest, q.structure, q.composition, q.render.html,
    q.prepress, q.external, q.default -- that nothing in the codebase read
    before this package). A new stage's queue is picked up automatically
    here; nothing to fall out of sync with stages/*.py the way a static
    stage_name -> queue map would (see tools/lint_docs_claims.py's whole
    reason for existing: a second, hand-maintained copy of a fact the source
    already states is exactly what drifts).
    """
    return tuple(sorted({d.queue for d in registry.all()}))


__all__ = ["discover_queues"]
