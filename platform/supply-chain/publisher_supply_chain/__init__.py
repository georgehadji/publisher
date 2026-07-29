"""
Publisher Supply Chain Security — SBOM generation, dependency scanning, CVE gates.

From BUILD_PLAN.md §5 P5:
- SBOM/signing/CVE gates
- Critical CVEs in shipped images: 0 (Trivy gate)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── SBOM types ──────────────────────────────────────────────────

class Dependency:
    """A software dependency with version and license info."""
    def __init__(self, name: str, version: str, ecosystem: str,
                 license_ref: str = "unknown", purl: str = ""):
        self.name = name
        self.version = version
        self.ecosystem = ecosystem
        self.license_ref = license_ref
        self.purl = purl or f"pkg:{ecosystem}/{name}@{version}"
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "ecosystem": self.ecosystem,
            "license": self.license_ref,
            "purl": self.purl,
        }


class SBOM:
    """
    Software Bill of Materials for a build.
    
    Tracks every dependency across all three languages:
    - Python (pip packages via pyproject.toml)
    - Node/TypeScript (npm packages via package.json)
    - Rust (cargo crates via Cargo.toml)
    - System (Docker image digests, system packages)
    """
    
    def __init__(self, build_id: str):
        self.build_id = build_id
        self._dependencies: list[Dependency] = []
        self._images: dict[str, str] = {}  # name -> digest
    
    def add_python(self, name: str, version: str):
        self._dependencies.append(Dependency(name, version, "pypi"))
    
    def add_node(self, name: str, version: str):
        self._dependencies.append(Dependency(name, version, "npm"))
    
    def add_rust(self, name: str, version: str):
        self._dependencies.append(Dependency(name, version, "cargo"))
    
    def add_image(self, name: str, digest: str):
        self._images[name] = digest
    
    def to_dict(self) -> dict:
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "serialNumber": f"urn:uuid:{hashlib.sha256(self.build_id.encode()).hexdigest()[:32]}",
            "version": 1,
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tools": [{"name": "publisher-sbom", "version": "0.1.0"}],
                "component": {
                    "name": "publisher",
                    "version": "0.1.0",
                    "type": "application",
                },
            },
            "components": [d.to_dict() for d in self._dependencies],
            "images": self._images,
        }


class CveGate:
    """
    CVE gate — checks dependencies against known vulnerabilities.
    
    In production, integrates with Trivy/Grype.
    In the tracer bullet, provides a structured pass/fail.
    """
    
    def __init__(self):
        self._known_cves: dict[str, list[dict]] = {}
    
    def scan(self, sbom: SBOM) -> "CveResult":
        """Scan SBOM components for known CVEs."""
        findings = []
        
        for dep in sbom._dependencies:
            # Check against known CVE database (stub)
            if dep.name in self._known_cves:
                for cve in self._known_cves[dep.name]:
                    findings.append({
                        "id": cve["id"],
                        "severity": cve.get("severity", "unknown"),
                        "package": dep.name,
                        "version": dep.version,
                        "fix_version": cve.get("fix_version", "unknown"),
                    })
        
        critical = [f for f in findings if f["severity"] == "critical"]
        high = [f for f in findings if f["severity"] == "high"]
        
        return CveResult(
            total_scanned=len(sbom._dependencies),
            findings=findings,
            critical_count=len(critical),
            high_count=len(high),
            passed=len(critical) == 0,  # Gate: zero critical CVEs
        )


class CveResult:
    """Result of a CVE scan."""
    def __init__(self, total_scanned: int, findings: list[dict],
                 critical_count: int, high_count: int, passed: bool):
        self.total_scanned = total_scanned
        self.findings = findings
        self.critical_count = critical_count
        self.high_count = high_count
        self.passed = passed
    
    def to_dict(self) -> dict:
        return {
            "totalScanned": self.total_scanned,
            "findings": self.findings,
            "criticalCount": self.critical_count,
            "highCount": self.high_count,
            "passed": self.passed,
        }


def generate_sbom(build_id: str, project_root: str | Path = ".") -> SBOM:
    """Generate an SBOM by scanning project dependency files."""
    root = Path(project_root)
    sbom = SBOM(build_id)
    
    # Scan Python dependencies (pyproject.toml)
    for pyproject in root.rglob("pyproject.toml"):
        if "node_modules" in str(pyproject) or ".venv" in str(pyproject):
            continue
        import tomllib
        try:
            data = tomllib.loads(pyproject.read_text())
            if deps := data.get("project", {}).get("dependencies", []):
                for dep in deps:
                    parts = dep.replace(">=", "@").replace("==", "@").split("@")
                    name = parts[0].strip()
                    version = parts[1].strip() if len(parts) > 1 else "unknown"
                    sbom.add_python(name, version)
        except Exception:
            pass
    
    # Scan Node dependencies (package.json)
    for pkg_json in root.rglob("package.json"):
        if "node_modules" in str(pkg_json):
            continue
        try:
            data = json.loads(pkg_json.read_text())
            for dep_type in ("dependencies", "devDependencies"):
                for name, version in data.get(dep_type, {}).items():
                    sbom.add_node(name, version.lstrip("^~"))
        except Exception:
            pass
    
    # Scan Rust dependencies (Cargo.toml)
    for cargo in root.rglob("Cargo.toml"):
        try:
            data = tomllib.loads(cargo.read_text())
            if deps := data.get("dependencies", {}):
                for name, version_info in deps.items():
                    if isinstance(version_info, str):
                        sbom.add_rust(name, version_info)
                    elif isinstance(version_info, dict):
                        sbom.add_rust(name, version_info.get("version", "unknown"))
        except Exception:
            pass
    
    return sbom
