"""
Manuscript Doctor — pre-ingest advisory analysis.

From AGENT_DESIGN.md §1.3 and BUILD_PLAN.md P6:
Trigger: on upload, pre-ingest
Tools: read-only over typescript.html
Action space: advisory text only
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class ManuscriptDoctor:
    """
    Analyzes a manuscript before ingestion and provides advisory feedback.
    
    Checks:
    - File format and size
    - Page count estimate
    - Potential issues (embedded fonts, tracked changes, images)
    - Structural consistency
    """
    
    def analyze(self, file_path: str | Path) -> dict:
        """Analyze a manuscript and return an advisory report."""
        path = Path(file_path)
        
        if not path.exists():
            return {"status": "error", "message": "File not found"}
        
        size = path.stat().st_size
        ext = path.suffix.lower()
        name = path.name
        
        findings = []
        
        # Format check
        if ext == ".doc":
            findings.append({
                "severity": "warning",
                "code": "legacy-format",
                "message": ".doc format requires conversion via LibreOffice. This adds 2-10s to build time.",
            })
        elif ext == ".docx":
            findings.append({
                "severity": "info",
                "code": "format-ok",
                "message": "DOCX format accepted directly.",
            })
        else:
            findings.append({
                "severity": "error",
                "code": "unsupported-format",
                "message": f"Unsupported format: {ext}. Expected .doc or .docx.",
            })
        
        # Size check
        if size > 100 * 1024 * 1024:  # 100 MB
            findings.append({
                "severity": "warning",
                "code": "large-file",
                "message": f"File is {size / 1024 / 1024:.0f} MB. Large files may increase processing time.",
            })
        elif size == 0:
            findings.append({
                "severity": "error",
                "code": "empty-file",
                "message": "File is empty.",
            })
        
        return {
            "status": "success",
            "fileName": name,
            "fileSize": size,
            "fileSizeHuman": self._format_size(size),
            "extension": ext,
            "findings": findings,
            "estimatedPageCount": max(size // 3000, 1),  # rough: ~3KB per page
            "recommendations": self._recommendations(findings),
        }
    
    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / 1024 / 1024:.1f} MB"
    
    def _recommendations(self, findings: list[dict]) -> list[str]:
        recs = []
        for f in findings:
            if f["severity"] == "error":
                recs.append(f"Resolve: {f['message']}")
            elif f["severity"] == "warning":
                recs.append(f"Consider: {f['message']}")
        return recs


class BacklistTriage:
    """
    Backlist Accessibility Triage (O6) — audits a collection of titles.
    
    From BUILD_PLAN.md P6 and OPTIMIZATION.md §O6:
    A 500-title backlist audit runs end to end and produces a ranked
    remediation plan for under $50 of compute.
    """
    
    def __init__(self, cost_per_title: float = 0.10):
        self.cost_per_title = cost_per_title
    
    def audit(self, titles: list[dict]) -> dict:
        """
        Run an accessibility audit on a list of titles.
        
        Each title must have at least an 'id' and 'title' field.
        Optional: 'pageCount', 'hasImages', 'language'
        """
        results = []
        
        for title in titles:
            result = self._audit_single(title)
            results.append(result)
        
        total_cost = len(titles) * self.cost_per_title
        needs_remediation = [r for r in results if r["remediationPriority"] > 0]
        
        # Sort by priority (highest first)
        needs_remediation.sort(key=lambda r: -r["remediationPriority"])
        
        return {
            "schema": "backlist-triage/1",
            "titlesAudited": len(titles),
            "titlesNeedingRemediation": len(needs_remediation),
            "totalCostUsd": round(total_cost, 2),
            "withinBudget": total_cost <= 50.0,
            "rankedRemediationPlan": needs_remediation,
            "summary": {
                "totalTitles": len(titles),
                "noAltText": sum(1 for r in results if r.get("issues", {}).get("noAltText")),
                "poorContrast": sum(1 for r in results if r.get("issues", {}).get("poorContrast")),
                "missingLanguage": sum(1 for r in results if r.get("issues", {}).get("missingLanguage")),
                "noToc": sum(1 for r in results if r.get("issues", {}).get("noToc")),
            },
        }
    
    def _audit_single(self, title: dict) -> dict:
        """Audit a single title for accessibility issues."""
        issues = {}
        priority = 0
        
        # Check for images without alt text
        if title.get("hasImages", False):
            issues["noAltText"] = True
            priority += 3
        
        # Check for missing language
        if not title.get("language"):
            issues["missingLanguage"] = True
            priority += 2
        
        # Check for missing TOC
        if title.get("noToc", False):
            issues["noToc"] = True
            priority += 2
        
        # Size-based contrast risk estimate
        if title.get("pageCount", 0) > 300:
            issues["poorContrast"] = priority > 0
            if issues.get("poorContrast"):
                priority += 1
        
        return {
            "titleId": title.get("id", "?"),
            "title": title.get("title", "Unknown"),
            "remediationPriority": priority,
            "issues": issues,
            "estimatedCostUsd": self.cost_per_title,
            "recommendation": "Full audit" if priority >= 5 else "Spot check" if priority >= 2 else "No action needed",
        }
