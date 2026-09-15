"""
E3.2 acceptance (docs/ARCHITECTURE_SCORE_10_PLAN.md).

Before this: `SandboxTier`, `ExitReason`, `SandboxResult` and the rest of
`publisher_sandbox` were imported by nothing but their own tests -- the
object-capability model the module's docstring describes had no call site.
These tests exercise the real `SandboxPort` implementations directly (not
through a stage), the same way `test_middleware.py` exercises `deadline_mw`/
`memory_mw` directly (E2.2) rather than only through a full pipeline run.
"""

from __future__ import annotations

import os
import sys

import pytest

from publisher_sandbox import (
    ExitReason, InProcessSandbox, ResourceBudget, RlimitSubprocessSandbox,
    SandboxTier, sandbox_for,
)

_BUDGET = ResourceBudget(memory_mb=512, cpu_seconds=30, wall_clock_s=10)


def test_in_process_sandbox_runs_a_real_command(tmp_path):
    result = InProcessSandbox().run(
        [sys.executable, "-c", "print('hi')"],
        input_dir=tmp_path, output_dir=tmp_path, budget=_BUDGET, tier=SandboxTier.LIGHT,
    )
    assert result.exit_code == 0
    assert result.reason == ExitReason.SUCCESS
    assert "hi" in result.stdout


def test_in_process_sandbox_reports_a_nonzero_exit_as_crash(tmp_path):
    result = InProcessSandbox().run(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        input_dir=tmp_path, output_dir=tmp_path, budget=_BUDGET, tier=SandboxTier.LIGHT,
    )
    assert result.exit_code == 3
    assert result.reason == ExitReason.CRASH


def test_rlimit_sandbox_refuses_heavy_tier_with_security(tmp_path):
    # Cross-platform: the HEAVY/EXTERNAL refusal is checked before the POSIX
    # check, so this runs (and must pass) on Windows too.
    result = RlimitSubprocessSandbox().run(
        [sys.executable, "-c", "print('should never run')"],
        input_dir=tmp_path, output_dir=tmp_path, budget=_BUDGET, tier=SandboxTier.HEAVY,
    )
    assert result.reason == ExitReason.SECURITY
    assert result.exit_code != 0


def test_rlimit_sandbox_refuses_external_tier_with_security(tmp_path):
    result = RlimitSubprocessSandbox().run(
        [sys.executable, "-c", "print('should never run')"],
        input_dir=tmp_path, output_dir=tmp_path, budget=_BUDGET, tier=SandboxTier.EXTERNAL,
    )
    assert result.reason == ExitReason.SECURITY


@pytest.mark.skipif(os.name != "nt", reason="this is specifically the non-POSIX fallback path")
def test_rlimit_sandbox_refuses_standard_tier_on_windows_with_security(tmp_path):
    result = RlimitSubprocessSandbox().run(
        [sys.executable, "-c", "print('should never run')"],
        input_dir=tmp_path, output_dir=tmp_path, budget=_BUDGET, tier=SandboxTier.STANDARD,
    )
    assert result.reason == ExitReason.SECURITY


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only -- setrlimit/prctl do not exist on Windows")
def test_rlimit_sandbox_runs_a_real_command_under_standard_tier(tmp_path):
    result = RlimitSubprocessSandbox().run(
        [sys.executable, "-c", "print('hi')"],
        input_dir=tmp_path, output_dir=tmp_path, budget=_BUDGET, tier=SandboxTier.STANDARD,
    )
    assert result.exit_code == 0
    assert result.reason == ExitReason.SUCCESS
    assert "hi" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only -- resource.RLIMIT_AS does not exist on Windows")
def test_rlimit_sandbox_kills_a_process_that_exceeds_its_memory_budget(tmp_path):
    import resource

    baseline_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    budget = ResourceBudget(memory_mb=baseline_mb + 20, cpu_seconds=30, wall_clock_s=10)

    result = RlimitSubprocessSandbox().run(
        [sys.executable, "-c", "bytearray(200 * 1024 * 1024)"],
        input_dir=tmp_path, output_dir=tmp_path, budget=budget, tier=SandboxTier.STANDARD,
    )
    assert result.exit_code != 0
    assert result.reason == ExitReason.CRASH


def test_sandbox_for_picks_the_real_sandbox_on_posix_and_in_process_elsewhere():
    if os.name == "posix":
        assert isinstance(sandbox_for(), RlimitSubprocessSandbox)
    else:
        assert isinstance(sandbox_for(), InProcessSandbox)
