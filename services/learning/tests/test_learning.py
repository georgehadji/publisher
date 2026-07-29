"""Tests for P7 — Learning system."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "publisher_learning"))

from publisher_learning import (
    L1Memory, L2Consolidation, L3Distillation,
    JudgeCalibrationHarness, HoldoutSlice, RatchetCI,
    LearnedPattern, PatternType, PromotionRecord,
    LearningLevel,
)


# ── L1 Memory tests ────────────────────────────────────────────

class TestL1Memory:
    def test_record_pattern(self):
        l1 = L1Memory()
        p = LearnedPattern(
            pattern_type=PatternType.CORRECTION,
            scope="title",
            source_title_id="title-1",
            pattern_data={"classification": "chapter-title"},
            confidence=0.85,
        )
        pid = l1.record(p)
        assert pid.startswith("ptn-")
        assert l1.pattern_count == 1
    
    def test_record_deduplicates(self):
        l1 = L1Memory()
        p1 = LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="title-1",
            pattern_data={"classification": "chapter-title"},
            confidence=0.85,
        )
        p2 = LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="title-1",
            pattern_data={"classification": "chapter-title"},
            confidence=0.9,
        )
        pid1 = l1.record(p1)
        pid2 = l1.record(p2)
        assert pid1 == pid2  # Deduplicated
        assert l1.pattern_count == 1
    
    def test_query_by_scope(self):
        l1 = L1Memory()
        l1.record(LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="t1", pattern_data={}, confidence=0.8,
        ))
        l1.record(LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="t2", pattern_data={}, confidence=0.8,
        ))
        results = l1.query(scope="title", scope_id="t1")
        assert len(results) == 1
    
    def test_forgetting_policy(self):
        import datetime
        l1 = L1Memory()
        old = LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="t1", pattern_data={}, confidence=0.5,
            last_applied="2020-01-01T00:00:00",
        )
        l1.record(old)
        archived = l1.apply_forgetting()
        assert archived >= 1
    
    def test_active_pattern_count(self):
        l1 = L1Memory()
        l1.record(LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="t1", pattern_data={"classification": "chapter-title"},
            confidence=0.9,
        ))
        l1.record(LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="t2", pattern_data={"classification": "heading"},
            confidence=0.1,
        ))
        assert l1.active_pattern_count >= 1


# ── L2 Consolidation tests ─────────────────────────────────────

class TestL2Consolidation:
    def test_consolidation_creates_exemplars(self):
        l1 = L1Memory()
        # Add several patterns with same classification
        for i in range(4):
            l1.record(LearnedPattern(
                pattern_type=PatternType.CORRECTION, scope="title",
                source_title_id=f"t{i}", pattern_data={"classification": "chapter-title"},
                confidence=0.8, frequency=5,
            ))
        
        l2 = L2Consolidation(l1)
        result = l2.run()
        assert result["exemplars_created"] >= 1
        assert result["patterns_processed"] >= 4
    
    def test_consolidation_retires_low_quality(self):
        l1 = L1Memory()
        l1.record(LearnedPattern(
            pattern_type=PatternType.CORRECTION, scope="title",
            source_title_id="t1", pattern_data={},
            confidence=0.1, frequency=1,
        ))
        l2 = L2Consolidation(l1)
        result = l2.run()
        assert result["patterns_retired"] >= 1


# ── L3 Distillation tests ──────────────────────────────────────

class TestL3Distillation:
    def test_promotion_rejected_few_tenants(self):
        l3 = L3Distillation()
        exemplar = LearnedPattern(
            pattern_type=PatternType.EXEMPLAR, scope="tenant",
            pattern_data={"classification": "test"},
            confidence=0.9,
        )
        tenant_patterns = [
            LearnedPattern(pattern_type=PatternType.CORRECTION, scope="title",
                           source_tenant_id="t1", pattern_data={}, confidence=0.5),
        ]
        record = l3.promote_to_global(exemplar, tenant_patterns)
        assert not record.passed_ratchet  # Not enough tenants
    
    def test_promotion_passes(self):
        l3 = L3Distillation()
        exemplar = LearnedPattern(
            pattern_type=PatternType.EXEMPLAR, scope="tenant",
            pattern_data={"classification": "test"},
            confidence=0.9,
        )
        tenant_patterns = [
            LearnedPattern(pattern_type=PatternType.CORRECTION, scope="title",
                           source_tenant_id=f"t{i}", pattern_data={}, confidence=0.5)
            for i in range(5)
        ]
        record = l3.promote_to_global(exemplar, tenant_patterns)
        assert record.passed_ratchet
    
    def test_leakage_detected(self):
        l3 = L3Distillation()
        exemplar = LearnedPattern(
            pattern_type=PatternType.EXEMPLAR, scope="tenant",
            pattern_data={"classification": "tenant_123_secret"},
            confidence=0.9,
        )
        tenant_patterns = [
            LearnedPattern(pattern_type=PatternType.CORRECTION, scope="title",
                           source_tenant_id=f"t{i}", pattern_data={}, confidence=0.5)
            for i in range(5)
        ]
        record = l3.promote_to_global(exemplar, tenant_patterns)
        assert not record.passed_ratchet  # Leakage detected


# ── Judge Calibration tests ────────────────────────────────────

class TestJudgeCalibration:
    def test_exact_match(self):
        harness = JudgeCalibrationHarness()
        ev = harness.evaluate(
            dimension="classification",
            judge_output={"score": 0.95, "label": "chapter-title"},
            expert_label={"score": 0.95, "label": "chapter-title"},
        )
        assert ev.agreement == 1.0
        assert ev.passed
    
    def test_partial_match(self):
        harness = JudgeCalibrationHarness()
        ev = harness.evaluate(
            dimension="classification",
            judge_output={"score": 0.8, "label": "heading"},
            expert_label={"score": 0.95, "label": "chapter-title"},
        )
        assert ev.agreement < 1.0
    
    def test_per_dimension_scores(self):
        harness = JudgeCalibrationHarness()
        harness.evaluate("dim1", {"score": 0.9}, {"score": 0.9})
        harness.evaluate("dim2", {"score": 0.5}, {"score": 1.0})
        scores = harness.per_dimension_scores
        assert "dim1" in scores
        assert "dim2" in scores


# ── Holdout Slice tests ────────────────────────────────────────

class TestHoldoutSlice:
    def test_holdout_ratio(self):
        hs = HoldoutSlice(holdout_ratio=0.1)
        assert hs.ratio == 0.1
    
    def test_assign_holdout(self):
        hs = HoldoutSlice(holdout_ratio=0.5)
        titles = [f"title-{i}" for i in range(10)]
        holdout = hs.assign(titles)
        assert len(holdout) == 5  # 50% of 10
    
    def test_invalid_ratio(self):
        import pytest
        with pytest.raises(ValueError):
            HoldoutSlice(holdout_ratio=0.0)
        with pytest.raises(ValueError):
            HoldoutSlice(holdout_ratio=0.6)
    
    def test_is_holdout(self):
        hs = HoldoutSlice(holdout_ratio=0.5)
        titles = [f"t{i}" for i in range(10)]
        holdout = set(hs.assign(titles))
        for tid in holdout:
            assert hs.is_holdout(tid)
    
    def test_sealed_after_assign(self):
        hs = HoldoutSlice(holdout_ratio=0.5)
        first = set(hs.assign([f"t{i}" for i in range(10)]))
        second = set(hs.assign([f"t{i}" for i in range(10)]))
        assert first == second  # Stable


# ── Ratchet CI tests ───────────────────────────────────────────

class TestRatchetCI:
    def test_no_regression(self):
        ci = RatchetCI()
        ci.register_golden("case-1", {"accuracy": 0.95, "recall": 0.90})
        passed = ci.check("case-1", {"accuracy": 0.96, "recall": 0.91})
        assert passed
        assert not ci.has_regressions
    
    def test_regression_detected(self):
        ci = RatchetCI()
        ci.register_golden("case-1", {"accuracy": 0.95})
        passed = ci.check("case-1", {"accuracy": 0.80})
        assert not passed
        assert ci.has_regressions
        assert ci.regression_count == 1
    
    def test_no_golden_no_check(self):
        ci = RatchetCI()
        passed = ci.check("unknown", {})
        assert passed  # No golden = always pass
    
    def test_missing_key(self):
        ci = RatchetCI()
        ci.register_golden("case-1", {"accuracy": 0.95, "recall": 0.90})
        passed = ci.check("case-1", {"accuracy": 0.95})  # missing recall
        assert not passed
