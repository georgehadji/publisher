"""Tests for P5 — Scale and hardening."""

import json
import tempfile
from pathlib import Path

import pytest

# ── Sandbox tests ───────────────────────────────────────────────
from publisher_sandbox import (
    Sandbox, SandboxConfig, SandboxTier, ThreatMonitor, ExitReason,
    TIER_CONFIGS, create_sandbox,
    create_zip_bomb, create_xxe_fixture, create_billion_laughs,
    create_path_traversal_zip, create_fork_bomb_script,
)


class TestThreatMonitor:
    def test_zip_bomb_high_ratio(self):
        t = ThreatMonitor(SandboxConfig())
        t.check_zip_bomb(file_size=500_000_000, compressed_size=1000, entry_count=5)
        assert t.has_violations

    def test_zip_bomb_many_entries(self):
        t = ThreatMonitor(SandboxConfig())
        t.check_zip_bomb(file_size=1000, compressed_size=500, entry_count=99999)
        assert t.has_violations

    def test_xxe_doctype_with_internal(self):
        t = ThreatMonitor(SandboxConfig())
        t.check_xxe("<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>")
        assert t.has_violations

    def test_xxe_billion_laughs(self):
        t = ThreatMonitor(SandboxConfig())
        t.check_xxe("<!ENTITY lol " * 20)
        assert t.has_violations

    def test_path_traversal(self):
        t = ThreatMonitor(SandboxConfig())
        t.check_path_traversal("../../../etc/shadow")
        assert t.has_violations

    def test_clean_input_passes(self):
        t = ThreatMonitor(SandboxConfig())
        t.check_zip_bomb(file_size=1000, compressed_size=2000, entry_count=5)
        t.check_xxe("<root>normal</root>")
        t.check_path_traversal("safe/path/file.txt")
        assert not t.has_violations


class TestSandboxLifecycle:
    def test_context_manager(self):
        with Sandbox() as s:
            assert s.work_dir.exists()
            assert (s.work_dir / "input").exists()
            assert (s.work_dir / "output").exists()

    def test_cleanup_on_exit(self):
        wd = None
        with Sandbox() as s:
            wd = s.work_dir
        assert not wd.exists()

    def test_copy_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.txt"
            src.write_text("data")
            with Sandbox() as s:
                dest = s.copy_input(src)
                assert dest.read_text() == "data"


class TestTierConfigs:
    def test_light_tier_config(self):
        cfg = TIER_CONFIGS[SandboxTier.LIGHT]
        assert cfg.memory_max_mb == 256
        assert cfg.cpu_max == 1.0
        assert cfg.network_enabled is False

    def test_standard_tier_config(self):
        cfg = TIER_CONFIGS[SandboxTier.STANDARD]
        assert cfg.memory_max_mb == 512

    def test_heavy_tier_config(self):
        cfg = TIER_CONFIGS[SandboxTier.HEAVY]
        assert cfg.memory_max_mb == 2048
        assert cfg.cpu_max == 4.0

    def test_external_tier_config(self):
        cfg = TIER_CONFIGS[SandboxTier.EXTERNAL]
        assert cfg.network_enabled is True


class TestSecurityFixtures:
    def test_zip_bomb_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = create_zip_bomb(Path(tmp))
            assert p.exists()
            assert p.stat().st_size > 0

    def test_xxe_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = create_xxe_fixture(Path(tmp))
            assert "SYSTEM" in p.read_text()

    def test_billion_laughs_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = create_billion_laughs(Path(tmp))
            assert "lol6" in p.read_text()

    def test_path_traversal_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = create_path_traversal_zip(Path(tmp))
            assert p.exists()

    def test_fork_bomb_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = create_fork_bomb_script(Path(tmp))
            assert p.exists()
            assert p.stat().st_size > 0


# ── Reproducibility tests ───────────────────────────────────────
from publisher_reproducibility import ReproducibilityChecker


class TestReproducibility:
    def test_no_output_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp) / "output"
            build_dir.mkdir()
            checker = ReproducibilityChecker(golden_dir=tmp, build_output_dir=build_dir)
            report = checker.run()
            assert report is not None
            assert not report.passed  # No manifest

    def test_with_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp) / "output"
            build_dir.mkdir()
            manifest = {
                "buildId": "test-001",
                "toolchain": {
                    "engines": {"gs": "10.04"},
                    "images": {"ingest": "sha256:abc123"},
                },
                "stageVersions": {"paginate": 1, "finish": 1},
                "stages": [],
                "reproductionKey": "test-key-" + "a" * 50,
            }
            (build_dir / "build-manifest.json").write_text(json.dumps(manifest))
            checker = ReproducibilityChecker(golden_dir=tmp, build_output_dir=build_dir)
            report = checker.run()
            assert report.passed

    def test_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp) / "output"
            build_dir.mkdir()
            (build_dir / "build-manifest.json").write_text(json.dumps({
                "buildId": "test", "toolchain": {"engines": {}},
                "stages": [], "reproductionKey": "x" * 64,
            }))
            checker = ReproducibilityChecker(tmp, build_dir)
            report = checker.run()
            s = report.summary
            assert s["total_checks"] >= 3


# ── Supply chain / SBOM tests ───────────────────────────────────
from publisher_supply_chain import (
    SBOM, Dependency, CveGate, generate_sbom,
)


class TestSBOM:
    def test_add_dependency(self):
        sbom = SBOM("test-001")
        sbom.add_python("flask", "3.0.0")
        sbom.add_node("react", "18.0.0")
        assert len(sbom._dependencies) == 2

    def test_to_dict(self):
        sbom = SBOM("test-002")
        sbom.add_node("zod", "3.24.0")
        d = sbom.to_dict()
        assert d["bomFormat"] == "CycloneDX"
        assert len(d["components"]) == 1

    def test_add_image(self):
        sbom = SBOM("test-003")
        sbom.add_image("ingest", "sha256:abc123")
        d = sbom.to_dict()
        assert "ingest" in d["images"]


class TestCveGate:
    def test_scan_clean(self):
        sbom = SBOM("test-004")
        sbom.add_python("flask", "3.0.0")
        gate = CveGate()
        result = gate.scan(sbom)
        assert result.passed
        assert result.critical_count == 0

    def test_scan_with_critical(self):
        sbom = SBOM("test-005")
        sbom.add_python("bad-package", "1.0.0")
        gate = CveGate()
        gate._known_cves["bad-package"] = [
            {"id": "CVE-2025-0001", "severity": "critical"},
        ]
        result = gate.scan(sbom)
        assert not result.passed
        assert result.critical_count == 1


class TestGenerateSBOM:
    """Skip removed: the stated precondition ("requires full project dependency files")
    holds in this repo — generate_sbom resolves 70+ dependencies from the committed
    manifests. An unconditional skip left the supply-chain generator with zero executed
    coverage while the sibling CVE/SBOM classes implied it was covered."""

    def test_generate_from_root(self):
        sbom = generate_sbom("test-006")
        assert len(sbom._dependencies) >= 1

    def test_sbom_is_wellformed_cyclonedx(self):
        doc = generate_sbom("test-007").to_dict()
        assert doc["bomFormat"] == "CycloneDX"
        assert doc["components"], "SBOM lists no components"
        for c in doc["components"][:5]:
            # A component without a name or purl cannot be matched against a CVE feed,
            # which is the only reason the SBOM exists.
            assert c.get("name"), f"component missing name: {c}"
            assert c.get("purl", "").startswith("pkg:"), f"component missing purl: {c}"
