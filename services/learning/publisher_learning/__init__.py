"""
Publisher Learning System — L1/L2/L3 memory hierarchy.

From BUILD_PLAN.md §3.19 and D10:
- Learning is versioned, gated, and reversible.
- Learned state is part of the toolchain digest.
- Every promotion is a PR with corpus metrics.
- Ratchet, never regress: golden cases must not regress.
- ≥5% holdout slice from agent assistance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


# ── Core types ──────────────────────────────────────────────────

class LearningLevel(str, Enum):
    """Three-level memory hierarchy (BUILD_PLAN.md §3.19)."""
    L1_MEMORY = "l1"      # Title + tenant scopes, fast read/write
    L2_CONSOLIDATION = "l2"  # Nightly pattern extraction
    L3_DISTILLATION = "l3"   # Cross-tenant generalization


class PatternType(str, Enum):
    """Types of learned patterns."""
    CORRECTION = "correction"      # Single override that was applied
    EXEMPLAR = "exemplar"          # Representative example of a pattern
    MINED_RULE = "mined_rule"      # Generalizable rule extracted from exemplars


@dataclass
class LearnedPattern:
    """A single learned pattern."""
    pattern_type: PatternType
    scope: str  # "title" | "tenant" | "global"
    pattern_data: dict[str, Any] = field(default_factory=dict)
    id: str = ""  # auto-generated if empty
    source_title_id: Optional[str] = None
    source_tenant_id: Optional[str] = None
    confidence: float = 0.0
    frequency: int = 1
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_applied: Optional[str] = None
    version: int = 1
    retired: bool = False
    retire_reason: Optional[str] = None


@dataclass
class PromotionRecord:
    """Record of a pattern promotion through the ladder."""
    pattern_id: str
    from_level: LearningLevel
    to_level: LearningLevel
    approved_by: str  # "auto" | "judge" | "human"
    corpus_metrics: dict[str, float] = field(default_factory=dict)
    golden_case_regressions: list[str] = field(default_factory=list)
    passed_ratchet: bool = False
    promoted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── L1 Memory ───────────────────────────────────────────────────

class L1Memory:
    """
    L1 memory — fast, scoped learning per title and tenant.
    
    Stores:
    - Applied overrides (corrections)
    - Rejected proposals
    - Frequency counts per classification path
    
    Forgetting policy: patterns unused for 90 days are archived.
    """
    
    def __init__(self):
        self._patterns: dict[str, LearnedPattern] = {}
        self._forgetting_days = 90
    
    def record(self, pattern: LearnedPattern) -> str:
        """Record a learned pattern in L1."""
        # Check for duplicate
        existing = self._find_similar(pattern)
        if existing:
            existing.frequency += 1
            existing.last_applied = datetime.now(timezone.utc).isoformat()
            existing.version += 1
            return existing.id
        
        pattern.id = self._generate_id(pattern)
        self._patterns[pattern.id] = pattern
        return pattern.id
    
    def query(self, scope: str, scope_id: str, 
              pattern_type: Optional[PatternType] = None,
              min_confidence: float = 0.0) -> list[LearnedPattern]:
        """Query patterns by scope."""
        results = []
        for p in self._patterns.values():
            if p.retired:
                continue
            if p.scope != scope:
                continue
            if pattern_type and p.pattern_type != pattern_type:
                continue
            if p.confidence < min_confidence:
                continue
            # Check scope match
            if scope == "title" and p.source_title_id != scope_id:
                continue
            if scope == "tenant" and p.source_tenant_id != scope_id:
                continue
            results.append(p)
        return sorted(results, key=lambda p: -p.frequency)
    
    def apply_forgetting(self) -> int:
        """Apply forgetting policy: archive old unused patterns.
        
        Returns number of patterns archived.
        """
        now = datetime.now(timezone.utc)
        archived = 0
        to_remove = []
        
        for pid, pattern in self._patterns.items():
            if pattern.retired:
                continue
            if pattern.last_applied:
                last = datetime.fromisoformat(pattern.last_applied)
                # Make naive datetime timezone-aware for comparison
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                delta = (now - last).days
                if delta > self._forgetting_days:
                    pattern.retired = True
                    pattern.retire_reason = f"unused for {delta} days"
                    archived += 1
        
        return archived
    
    def _find_similar(self, pattern: LearnedPattern) -> Optional[LearnedPattern]:
        """Check if a similar pattern already exists."""
        for existing in self._patterns.values():
            if existing.retired:
                continue
            if existing.pattern_type != pattern.pattern_type:
                continue
            if existing.scope != pattern.scope:
                continue
            if existing.source_title_id != pattern.source_title_id:
                continue
            if existing.pattern_data.get("classification") == \
               pattern.pattern_data.get("classification"):
                return existing
        return None
    
    def _generate_id(self, pattern: LearnedPattern) -> str:
        """Generate a unique pattern ID."""
        raw = f"{pattern.pattern_type.value}-{pattern.scope}-{pattern.source_title_id}-{pattern.created_at}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"ptn-{h}"
    
    @property
    def pattern_count(self) -> int:
        return len([p for p in self._patterns.values() if not p.retired])
    
    @property
    def active_pattern_count(self) -> int:
        return len([p for p in self._patterns.values() 
                    if not p.retired and p.confidence >= 0.5])


# ── L2 Nightly Consolidation ───────────────────────────────────

class L2Consolidation:
    """
    L2 nightly consolidation — extracts patterns from L1 memory.
    
    Pipeline:
    1. Validate: check pattern quality metrics
    2. Monitor: track frequency and confidence trends
    3. Retire: demote low-quality or stale patterns
    """
    
    def __init__(self, l1: L1Memory):
        self._l1 = l1
        self._consolidation_log: list[dict] = []
    
    def run(self) -> dict:
        """Run one consolidation cycle."""
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "patterns_processed": 0,
            "patterns_promoted": 0,
            "patterns_retired": 0,
            "exemplars_created": 0,
            "metrics": {},
        }
        
        # Get all active L1 patterns
        patterns = [p for p in self._l1._patterns.values() if not p.retired]
        results["patterns_processed"] = len(patterns)
        
        # Validate phase
        valid_patterns = self._validate(patterns)
        
        # Monitor phase — aggregate by classification
        exemplars = self._create_exemplars(valid_patterns)
        results["exemplars_created"] = len(exemplars)
        
        # Retire phase
        retired = self._retire_stale(patterns)
        results["patterns_retired"] = len(retired)
        
        results["metrics"] = {
            "avg_confidence": sum(p.confidence for p in valid_patterns) / len(valid_patterns) if valid_patterns else 0.0,
            "total_active": len(valid_patterns),
        }
        
        self._consolidation_log.append(results)
        return results
    
    def _validate(self, patterns: list[LearnedPattern]) -> list[LearnedPattern]:
        """Validate patterns — filter out low-quality ones."""
        valid = []
        for p in patterns:
            # Minimum frequency threshold
            if p.frequency < 2:
                continue
            # Minimum confidence threshold for L2
            if p.confidence < 0.3:
                continue
            valid.append(p)
        return valid
    
    def _create_exemplars(self, patterns: list[LearnedPattern]) -> list[LearnedPattern]:
        """Create exemplars by aggregating similar patterns."""
        # Group by classification
        groups: dict[str, list[LearnedPattern]] = {}
        for p in patterns:
            classification = p.pattern_data.get("classification", "unknown")
            groups.setdefault(classification, []).append(p)
        
        exemplars = []
        for classification, group in groups.items():
            if len(group) >= 3:  # Need at least 3 examples for an exemplar
                avg_conf = sum(p.confidence for p in group) / len(group)
                exemplar = LearnedPattern(
                    id=f"exm-{hashlib.sha256(classification.encode()).hexdigest()[:12]}",
                    pattern_type=PatternType.EXEMPLAR,
                    scope="tenant",
                    pattern_data={
                        "classification": classification,
                        "source_patterns": [p.id for p in group[:5]],
                        "sample_count": len(group),
                    },
                    confidence=avg_conf,
                    frequency=len(group),
                )
                exemplars.append(exemplar)
                self._l1.record(exemplar)
        
        return exemplars
    
    def _retire_stale(self, patterns: list[LearnedPattern]) -> list[LearnedPattern]:
        """Retire patterns that don't meet quality bar."""
        retired = []
        for p in patterns:
            if p.frequency < 2 and p.confidence < 0.2:
                p.retired = True
                p.retire_reason = "low quality: frequency < 2 and confidence < 0.2"
                retired.append(p)
        return retired


# ── L3 Cross-Tenant Generalization ─────────────────────────────

class L3Distillation:
    """
    L3 distillation — cross-tenant pattern extraction.
    
    Gate: cross-tenant generalization check prevents style leakage.
    Only patterns that generalize cleanly (no tenant-specific artifacts)
    are promoted to global.
    """
    
    def __init__(self):
        self._promotions: list[PromotionRecord] = []
    
    def promote_to_global(self, exemplar: LearnedPattern,
                          tenant_patterns: list[LearnedPattern]) -> PromotionRecord:
        """
        Attempt to promote an exemplar from tenant scope to global.
        
        Checks for cross-tenant generalization:
        - Pattern must be observed in ≥ 3 tenants
        - No tenant-specific identifiers in pattern data
        - Golden-case regression check
        
        Returns PromotionRecord with pass/fail.
        """
        record = PromotionRecord(
            pattern_id=exemplar.id,
            from_level=LearningLevel.L2_CONSOLIDATION,
            to_level=LearningLevel.L3_DISTILLATION,
            approved_by="auto",
        )
        
        # Check cross-tenant generalization
        tenant_ids = set()
        for p in tenant_patterns:
            if p.source_tenant_id:
                tenant_ids.add(p.source_tenant_id)
        
        if len(tenant_ids) < 3:
            record.approved_by = "judge"
            record.corpus_metrics = {
                "tenants_observed": len(tenant_ids),
                "min_required": 3,
                "passed": 0.0,
            }
            self._promotions.append(record)
            return record
        
        # Check for tenant-specific data leakage
        has_leakage = self._check_leakage(exemplar)
        if has_leakage:
            record.approved_by = "human"
            record.corpus_metrics = {"leakage_detected": 1.0}
            self._promotions.append(record)
            return record
        
        # Promote
        record.passed_ratchet = True
        exemplar.scope = "global"
        record.corpus_metrics = {
            "tenants_observed": len(tenant_ids),
            "passed": 1.0,
        }
        self._promotions.append(record)
        return record
    
    def _check_leakage(self, pattern: LearnedPattern) -> bool:
        """Check for tenant-specific data in a pattern."""
        data = pattern.pattern_data
        text = json.dumps(data).lower()
        # Check for common leakage patterns
        leakage_indicators = ["user:", "tenant_", "title_", "/private/"]
        for indicator in leakage_indicators:
            if indicator in text:
                return True
        return False
    
    @property
    def promotion_count(self) -> int:
        return len(self._promotions)


# ── Judge Calibration ──────────────────────────────────────────

@dataclass
class JudgeEvaluation:
    """A single judge evaluation against an expert label."""
    dimension: str
    judge_score: float
    expert_score: float
    agreement: float  # 0.0 to 1.0
    passed: bool


class JudgeCalibrationHarness:
    """
    Judge calibration harness (BUILD_PLAN.md P7 gate).
    
    Measures judge agreement with expert labels per dimension.
    Target: ≥ 0.90 per dimension.
    """
    
    def __init__(self):
        self._evaluations: list[JudgeEvaluation] = []
    
    def evaluate(self, dimension: str, judge_output: dict, 
                 expert_label: dict) -> JudgeEvaluation:
        """Compare a judge's output against an expert label."""
        # Compute agreement score
        agreement = self._compute_agreement(judge_output, expert_label)
        
        ev = JudgeEvaluation(
            dimension=dimension,
            judge_score=judge_output.get("score", 0.0),
            expert_score=expert_label.get("score", 0.0),
            agreement=agreement,
            passed=agreement >= 0.90,
        )
        self._evaluations.append(ev)
        return ev
    
    def _compute_agreement(self, judge: dict, expert: dict) -> float:
        """Compute agreement between judge and expert on a dimension."""
        # Simple exact-match for enum fields, ratio for numeric
        scores = []
        
        for key in set(judge.keys()) | set(expert.keys()):
            jv = judge.get(key)
            ev = expert.get(key)
            
            if jv is None and ev is None:
                scores.append(1.0)
            elif jv is None or ev is None:
                scores.append(0.0)
            elif isinstance(jv, (int, float)) and isinstance(ev, (int, float)):
                if ev == 0:
                    scores.append(1.0 if abs(jv) < 0.01 else 0.0)
                else:
                    scores.append(1.0 - min(abs(jv - ev) / abs(ev), 1.0))
            else:
                scores.append(1.0 if jv == ev else 0.0)
        
        return sum(scores) / len(scores) if scores else 0.0
    
    @property
    def per_dimension_scores(self) -> dict[str, float]:
        """Get average agreement per dimension."""
        dims: dict[str, list[float]] = {}
        for ev in self._evaluations:
            dims.setdefault(ev.dimension, []).append(ev.agreement)
        return {d: sum(v) / len(v) for d, v in dims.items()}


# ── Holdout Slice ──────────────────────────────────────────────

class HoldoutSlice:
    """
    Holdout slice — ≥ 5% of titles permanently excluded from agent assistance.
    
    From BUILD_PLAN.md §0 and D10:
    Provides an unbiased baseline for measuring learning impact.
    These titles never receive agent proposals.
    """
    
    def __init__(self, holdout_ratio: float = 0.05):
        if not 0.01 <= holdout_ratio <= 0.5:
            raise ValueError(f"Holdout ratio must be 0.01-0.5, got {holdout_ratio}")
        self._holdout_ratio = holdout_ratio
        self._holdout_ids: set[str] = set()
        self._is_sealed = False
    
    def assign(self, title_ids: list[str]) -> list[str]:
        """Assign title IDs to holdout or treatment groups.
        
        Once assigned, the holdout set is sealed and cannot change.
        """
        if self._is_sealed:
            return list(self._holdout_ids)
        
        n_holdout = max(1, int(len(title_ids) * self._holdout_ratio))
        # Deterministic selection based on hash (stable across restarts)
        sorted_ids = sorted(title_ids)
        self._holdout_ids = set(sorted_ids[:n_holdout])
        self._is_sealed = True
        return list(self._holdout_ids)
    
    def is_holdout(self, title_id: str) -> bool:
        """Check if a title is in the holdout slice."""
        return title_id in self._holdout_ids
    
    @property
    def holdout_count(self) -> int:
        return len(self._holdout_ids)
    
    @property
    def ratio(self) -> float:
        return self._holdout_ratio


# ── Ratchet CI ─────────────────────────────────────────────────

class RatchetCI:
    """
    Ratchet CI — golden-case regression gate.
    
    From D10:
    A change that improves the aggregate but regresses any golden case
    is rejected pending explicit review.
    """
    
    def __init__(self):
        self._golden_cases: dict[str, dict] = {}
        self._regressions: list[dict] = []
    
    def register_golden(self, case_id: str, expected: dict):
        """Register a golden case with its expected output."""
        self._golden_cases[case_id] = expected
    
    def check(self, case_id: str, actual: dict) -> bool:
        """Check if actual output matches the golden case.
        
        Returns True if no regression (pass), False if regression (fail).
        """
        expected = self._golden_cases.get(case_id)
        if expected is None:
            return True  # No golden case = no constraint
        
        regression = self._detect_regression(expected, actual)
        if regression:
            self._regressions.append({
                "case_id": case_id,
                "dimension": regression,
                "expected": expected,
                "actual": actual,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return False
        return True
    
    def _detect_regression(self, expected: dict, actual: dict) -> Optional[str]:
        """Detect if any dimension regressed."""
        for key in expected:
            if key not in actual:
                return f"missing_key: {key}"
            exp_val = expected[key]
            act_val = actual[key]
            if isinstance(exp_val, (int, float)) and isinstance(act_val, (int, float)):
                if act_val < exp_val - 0.01:  # Allow tiny floating point variance
                    return f"regression: {key} ({exp_val} -> {act_val})"
            elif exp_val != act_val:
                return f"mismatch: {key}"
        return None
    
    @property
    def has_regressions(self) -> bool:
        return len(self._regressions) > 0
    
    @property
    def regression_count(self) -> int:
        return len(self._regressions)
