"""
R1 acceptance: `discover_queues()` must track stages/*.py's declared
`queue=` values exactly -- a hand-maintained copy of this set (the design
this test rules out) is exactly the kind of drift tools/lint_docs_claims.py
exists to catch elsewhere. This test imports the REAL registry (via `import
stages`, same as worker.py) rather than a synthetic one, so it fails the
moment a new stage's queue value is added without anyone deciding whether
it needs its own pool.
"""

from __future__ import annotations

import stages  # noqa: F401 -- registration side effect, same as worker.py
from publisher_stages import get_registry

from publisher_orchestration import discover_queues


# The 7-way taxonomy actually observed across stages/*.py's @stage(queue=...)
# declarations at the time this test was written (R1). Not exhaustive by
# design intent -- by exhaustive fact: every declared queue value in the repo
# today is one of these seven. A new, genuinely new eighth queue is a real
# capacity-planning decision (does it get its own pool?) that should be
# reviewed, not silently absorbed -- hence this is a closed set, and the
# test fails loudly if the registry ever reports one not listed here.
KNOWN_QUEUES = frozenset({
    "q.ingest", "q.structure", "q.composition", "q.render.html",
    "q.prepress", "q.external", "q.default",
})


def test_discover_queues_matches_the_real_registry_exactly():
    registry = get_registry()
    found = discover_queues(registry)
    assert set(found) == KNOWN_QUEUES, (
        f"stages/*.py's declared queue= values changed: {set(found)!r} vs "
        f"the known set {KNOWN_QUEUES!r}. If this is a deliberate new "
        "capability queue, update KNOWN_QUEUES here AND decide whether "
        "docker-compose.yml needs a new worker-temporal-* pool for it."
    )


def test_discover_queues_is_sorted_and_has_no_duplicates():
    registry = get_registry()
    found = discover_queues(registry)
    assert found == tuple(sorted(set(found)))


def test_discover_queues_is_empty_for_an_empty_registry():
    from publisher_stages import StageRegistry

    assert discover_queues(StageRegistry()) == ()
