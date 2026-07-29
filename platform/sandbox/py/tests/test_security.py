"""
Publisher Security Test Suite.

From BUILD_PLAN.md §3.4 and ARCHITECTURE.md §2.9:
Escape attempts ARE the test suite. Each fixture must fail closed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from publisher_sandbox import (
    Sandbox, SandboxConfig, SandboxTier, ThreatMonitor,
    ExitReason, TIER_CONFIGS, create_sandbox,
    create_zip_bomb, create_xxe_fixture, create_billion_laughs,
    create_path_traversal_zip, create_fork_bomb_script,
)


# ── Threat Monitor Tests ────────────────────────────────────────

class TestThreatMonitor:
    def test_zip_bomb_detection(self):
        monitor = ThreatMonitor(SandboxConfig())
        monitor.check_zip_bomb(file_size=100_000_000, compressed_size=1000, entry_count=10)
        assert monitor.has_violations
        assert any("zip_bomb" in v for v in monitor.violations)

    def test_zip_too_many_entries(self):
        monitor = ThreatMonitor(SandboxConfig())
        monitor.check_zip_bomb(file_size=1000, compressed_size=1000, entry_count=99999)
        assert monitor.has_violations
        assert any("zip_bomb" in v for v in monitor.violations)

    def test_xxe_detection(self):
        monitor = ThreatMonitor(SandboxConfig())
        monitor.check_xxe("<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>")
        assert monitor.has_violations
        assert any("xxe" in v for v in monitor.violations)

    def test_billion_laughs_detection(self):
        monitor = ThreatMonitor(SandboxConfig())
        content = "<!DOCTYPE lolz [" + "<!ENTITY lol " * 20 + ">]>"
        monitor.check_xxe(content)
        assert monitor.has_violations

    def test_path_traversal_detection(self):
        monitor = ThreatMonitor(SandboxConfig())
        monitor.check_path_traversal("../../etc/passwd")
        assert monitor.has_violations

    def test_absolute_path_detection(self):
        monitor = ThreatMonitor(SandboxConfig())
        monitor.check_path_traversal("/etc/passwd")
        assert monitor.has_violations

    def test_clean_file_passes(self):
        monitor = ThreatMonitor(SandboxConfig())
        monitor.check_zip_bomb(file_size=1000, compressed_size=2000, entry_count=5)
        assert not monitor.has_violations

    def test_no_false_positive_normal_xml(self):
        monitor = ThreatMonitor(SandboxConfig())
        monitor.check_xxe("<?xml version='1.0'?><root>Hello</root>")
        assert not monitor.has_violations


# ── Sandbox Tier Tests ──────────────────────────────────────────

class TestSandboxCreation:
    def test_create_light_sandbox(self):
        s = create_sandbox(SandboxTier.LIGHT)
        assert s is not None

    def test_create_standard_sandbox(self):
        s = create_sandbox(SandboxTier.STANDARD)
        assert s is not None

    def test_create_heavy_sandbox(self):
        s = create_sandbox(SandboxTier.HEAVY)
        assert s is not None

    def test_create_external_sandbox(self):
        s = create_sandbox(SandboxTier.EXTERNAL)
        assert s is not None

    def test_sandbox_context_manager(self):
        with Sandbox() as s:
            assert s.work_dir.exists()
            assert s.input_dir.exists()
            assert s.output_dir.exists()

    def test_sandbox_cleans_up(self):
        work_dir = None
        with Sandbox() as s:
            work_dir = s.work_dir
            assert work_dir.exists()
        assert not work_dir.exists() or not any(work_dir.iterdir())


# ── Fixture Creation Tests ──────────────────────────────────────

class TestSecurityFixtures:
    def test_create_zip_bomb(self):
        with tempfile.TemporaryDirectory() as tmp:
            bomb = create_zip_bomb(Path(tmp))
            assert bomb.exists()
            assert bomb.suffix == ".zip"

    def test_create_xxe_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            xxe = create_xxe_fixture(Path(tmp))
            assert xxe.exists()
            assert "SYSTEM" in xxe.read_text()

    def test_create_billion_laughs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bl = create_billion_laughs(Path(tmp))
            assert bl.exists()
            assert "lol6" in bl.read_text()

    def test_create_path_traversal_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            trav = create_path_traversal_zip(Path(tmp))
            assert trav.exists()

    def test_create_fork_bomb(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb = create_fork_bomb_script(Path(tmp))
            assert fb.exists()


# ── Integration: Run basic command in sandbox ───────────────────

@pytest.mark.skipif(os.name == "nt", reason="POSIX-only sandbox tests")
class TestSandboxExecution:
    def test_run_echo(self):
        with Sandbox() as s:
            result = s.run(["echo", "hello"])
            assert result.exit_code == 0
            assert result.reason == ExitReason.SUCCESS

    def test_run_failing_command(self):
        with Sandbox() as s:
            result = s.run(["sh", "-c", "exit 1"])
            assert result.exit_code == 1
            assert result.reason == ExitReason.CRASH

    def test_run_with_input(self):
        with Sandbox() as s:
            result = s.run(["cat"], input_data=b"hello stdin")
            assert result.exit_code == 0
            assert "hello stdin" in result.stdout

    def test_copy_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.txt"
            src.write_text("test data")
            with Sandbox() as s:
                dest = s.copy_input(src)
                assert dest.exists()
                assert dest.read_text() == "test data"

    def test_output_dir_writable(self):
        with Sandbox() as s:
            result = s.run(["sh", "-c", f"echo test > {s.output_dir / 'out.txt'}"])
            assert result.exit_code == 0
            assert (s.output_dir / "out.txt").exists()
