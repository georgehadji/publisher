"""
E2.1/E2.2/E2.3 acceptance (docs/ARCHITECTURE_SCORE_10_PLAN.md).

Before this: `tracer_bullet.py:281`-equivalent code set `deadline=datetime.now(utc)`
(the present instant -- always already expired) and nothing ever read
`ctx.deadline`; `memory_budget_mb` was threaded into every stage's context and
enforced nowhere; and `ctx.cache_key` was a fake placeholder
(`f"tb-{stage}-v{ver}"`, constant across every input) that had nothing to do
with the real cache key computed ~20 lines later in the same function. These
tests build a scratch registry with one synthetic stage each and run it
through the real `run()` executor -- no fixtures, no CAS content, just enough
to prove the middleware chain actually enforces what the declarations
promise.
"""

from __future__ import annotations

import os
import time

import pytest

from publisher_exec import plan, run
from publisher_stages import ErrorKind, StageDeclaration, StageError, StageRegistry, StageResult


def _registry_with(fn, *, timeout_s=300, memory_budget_mb=256) -> StageRegistry:
    registry = StageRegistry()
    registry.register(StageDeclaration(
        name="probe",
        version=1,
        fn=fn,
        outputs={"out": "probe-out/1"},
        timeout_s=timeout_s,
        memory_budget_mb=memory_budget_mb,
    ))
    return registry


def _run_once(registry: StageRegistry, tmp_path, build_id="probe-build"):
    execution_plan = plan(registry)
    return run(execution_plan, registry, build_id=build_id, cas_root=tmp_path / "cas")


def test_a_stage_that_sleeps_past_its_deadline_fails_with_timeout(tmp_path):
    def slow(ctx, **kw):
        time.sleep(0.3)
        return StageResult(artifacts=[])

    registry = _registry_with(slow, timeout_s=0.01)

    with pytest.raises(StageError) as exc_info:
        _run_once(registry, tmp_path)

    assert exc_info.value.kind == ErrorKind.TIMEOUT


def test_a_stage_within_its_deadline_succeeds(tmp_path):
    # Companion to the timeout test -- proves deadline_mw isn't just always
    # failing.
    def fast(ctx, **kw):
        return StageResult(artifacts=[])

    registry = _registry_with(fast, timeout_s=5)
    result = _run_once(registry, tmp_path)
    assert result["probe"].artifacts == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only -- resource.RLIMIT_AS does not exist on Windows")
def test_a_stage_that_exceeds_its_memory_budget_fails_with_resource_exhausted(tmp_path):
    # memory_budget_mb is headroom ON TOP OF current usage (memory_mw reads
    # current virtual size itself), not an absolute ceiling -- see
    # memory_mw's docstring. A small budget is enough: 200MB is always far
    # more than 20MB of additional headroom, regardless of this process's
    # own baseline footprint at the time the test runs.
    def hungry(ctx, **kw):
        _ = bytearray(200 * 1024 * 1024)
        return StageResult(artifacts=[])

    registry = _registry_with(hungry, memory_budget_mb=20)

    with pytest.raises(StageError) as exc_info:
        _run_once(registry, tmp_path)

    assert exc_info.value.kind == ErrorKind.RESOURCE_EXHAUSTED


def test_deterministic_seed_differs_for_different_inputs_and_repeats_for_identical_ones(tmp_path):
    captured: list[str] = []

    def probe(ctx, **kw):
        captured.append(ctx.deterministic_seed)
        return StageResult(artifacts=[])

    registry = StageRegistry()
    registry.register(StageDeclaration(
        name="probe", version=1, fn=probe,
        inputs={"payload": "payload/1"}, root_inputs=["payload"],
        outputs={"out": "probe-out/1"},
    ))

    initial_a = {"probe": {"payload": "A"}}
    initial_b = {"probe": {"payload": "B"}}
    # plan() and run() both need initial_inputs -- plan() to decide
    # reachability, run() to actually resolve the stage's keyword args (the
    # ExecutionPlan it returns deliberately carries nothing but stage order).
    run(plan(registry, initial_a), registry, build_id="b1",
        initial_inputs=initial_a, cas_root=tmp_path / "cas1")
    run(plan(registry, initial_a), registry, build_id="b2",
        initial_inputs=initial_a, cas_root=tmp_path / "cas2")
    run(plan(registry, initial_b), registry, build_id="b3",
        initial_inputs=initial_b, cas_root=tmp_path / "cas3")

    seed_a1, seed_a2, seed_b = captured
    # Identical inputs -> identical seed, regardless of build_id or wall clock.
    assert seed_a1 == seed_a2
    # Different inputs -> a different seed. This is exactly the property the
    # old `f"tb-{stage}-v{ver}"` placeholder lacked -- it was identical for
    # every build of "probe" v1 regardless of what payload it ran with.
    assert seed_a1 != seed_b


def test_cache_mw_serves_a_hit_on_the_second_identical_run(tmp_path):
    calls = []

    def probe(ctx, **kw):
        calls.append(1)
        return StageResult(artifacts=[])

    registry = _registry_with(probe)
    cas_root = tmp_path / "cas"

    first = run(plan(registry), registry, build_id="b1", cas_root=cas_root)
    second = run(plan(registry), registry, build_id="b2", cas_root=cas_root)

    assert first["probe"].cache_hit is False
    assert second["probe"].cache_hit is True
    assert len(calls) == 1  # the stage function itself ran exactly once
