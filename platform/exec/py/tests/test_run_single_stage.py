"""
R1 acceptance (docs/ARCHITECTURE_ROADMAP.md): `run_single_stage` must put a
stage through the SAME cache/memory/deadline chain `run()` uses, since it
exists precisely so the Temporal activity path (`platform/orchestration`)
never re-implements that chain a second time. These tests exercise it
directly, with no CAS engine dependency, no registry beyond one synthetic
stage -- the same style as test_middleware.py's `run()` tests, so a reader
can compare the two side by side.
"""

from __future__ import annotations

import pytest

from publisher_exec import run_single_stage
from publisher_cache import SqliteCacheStore
from publisher_stages import ErrorKind, StageDeclaration, StageError, StageRegistry, StageResult


def _registry_with(fn, *, timeout_s=300, memory_budget_mb=256, inputs=None) -> StageRegistry:
    registry = StageRegistry()
    registry.register(StageDeclaration(
        name="probe",
        version=1,
        fn=fn,
        inputs=inputs or {},
        outputs={"out": "probe-out/1"},
        timeout_s=timeout_s,
        memory_budget_mb=memory_budget_mb,
    ))
    return registry


def _cache(tmp_path) -> SqliteCacheStore:
    return SqliteCacheStore(tmp_path / "cache.sqlite")


def test_runs_a_stage_and_returns_its_own_output_paths(tmp_path):
    def probe(ctx, **kw):
        return StageResult(artifacts=[])

    registry = _registry_with(probe)
    result, own_paths = run_single_stage(
        "probe", registry, build_id="b1", resolved_inputs={},
        cas_root=tmp_path / "cas", cache_store=_cache(tmp_path),
    )
    assert result.artifacts == []
    assert own_paths == {}  # this probe emits no artifacts


def test_unknown_stage_name_raises_engine_bug(tmp_path):
    registry = StageRegistry()
    with pytest.raises(StageError) as exc_info:
        run_single_stage(
            "does-not-exist", registry, build_id="b1", resolved_inputs={},
            cas_root=tmp_path / "cas", cache_store=_cache(tmp_path),
        )
    assert exc_info.value.kind == ErrorKind.ENGINE_BUG


def test_enforces_the_same_deadline_middleware_as_run(tmp_path):
    import time

    def slow(ctx, **kw):
        time.sleep(0.3)
        return StageResult(artifacts=[])

    registry = _registry_with(slow, timeout_s=0.01)
    with pytest.raises(StageError) as exc_info:
        run_single_stage(
            "probe", registry, build_id="b1", resolved_inputs={},
            cas_root=tmp_path / "cas", cache_store=_cache(tmp_path),
        )
    assert exc_info.value.kind == ErrorKind.TIMEOUT


def test_cache_hit_on_the_second_identical_call_same_as_run(tmp_path):
    calls = []

    def probe(ctx, **kw):
        calls.append(1)
        return StageResult(artifacts=[])

    registry = _registry_with(probe)
    cas_root = tmp_path / "cas"
    cache_store = _cache(tmp_path)

    first, _ = run_single_stage(
        "probe", registry, build_id="b1", resolved_inputs={},
        cas_root=cas_root, cache_store=cache_store,
    )
    second, _ = run_single_stage(
        "probe", registry, build_id="b2", resolved_inputs={},
        cas_root=cas_root, cache_store=cache_store,
    )
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(calls) == 1


def test_resolved_inputs_are_passed_through_as_stage_kwargs(tmp_path):
    seen = {}

    def probe(ctx, **kw):
        seen.update(kw)
        return StageResult(artifacts=[])

    registry = _registry_with(probe, inputs={"doc_path": "doc-effective/1"})
    run_single_stage(
        "probe", registry, build_id="b1", resolved_inputs={"doc_path": "/tmp/x"},
        cas_root=tmp_path / "cas", cache_store=_cache(tmp_path),
    )
    assert seen == {"doc_path": "/tmp/x"}
