"""Tests for publisher_cover.judge -- deterministic gate policy and panel aggregation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publisher_cover.judge import (
    PairwiseVerdict,
    RasterMetrics,
    aggregate_panel,
    run_deterministic_gates,
    thumbnail_legibility_gate,
    type_zone_contrast_gate,
)


class TestThumbnailLegibilityGate:
    def test_passes_at_full_size(self):
        metrics = RasterMetrics(
            long_edge_px=2048, type_zone_mean_luminance_l=10.0, type_zone_luminance_stddev=2.0,
        )
        assert thumbnail_legibility_gate(metrics).passed

    def test_fails_below_minimum_thumbnail_size(self):
        metrics = RasterMetrics(
            long_edge_px=100, type_zone_mean_luminance_l=10.0, type_zone_luminance_stddev=2.0,
        )
        result = thumbnail_legibility_gate(metrics)
        assert not result.passed
        assert "160" in result.reason


class TestTypeZoneContrastGate:
    def test_passes_with_dark_zone_and_white_text(self):
        metrics = RasterMetrics(
            long_edge_px=2048, type_zone_mean_luminance_l=10.0, type_zone_luminance_stddev=3.0,
        )
        assert type_zone_contrast_gate(metrics).passed

    def test_fails_when_zone_luminance_close_to_text_luminance(self):
        metrics = RasterMetrics(
            long_edge_px=2048, type_zone_mean_luminance_l=90.0, type_zone_luminance_stddev=2.0,
            assumed_text_luminance_l=95.0,
        )
        result = type_zone_contrast_gate(metrics)
        assert not result.passed
        assert "contrast" in result.reason

    def test_fails_when_zone_is_too_busy_despite_good_mean_contrast(self):
        metrics = RasterMetrics(
            long_edge_px=2048, type_zone_mean_luminance_l=10.0, type_zone_luminance_stddev=40.0,
        )
        result = type_zone_contrast_gate(metrics)
        assert not result.passed
        assert "busy" in result.reason


class TestRunDeterministicGates:
    def test_all_pass_returns_ok(self):
        metrics = RasterMetrics(
            long_edge_px=2048, type_zone_mean_luminance_l=10.0, type_zone_luminance_stddev=3.0,
        )
        assert run_deterministic_gates(metrics).passed

    def test_first_failure_short_circuits(self):
        metrics = RasterMetrics(
            long_edge_px=50, type_zone_mean_luminance_l=90.0, type_zone_luminance_stddev=40.0,
        )
        result = run_deterministic_gates(metrics)
        assert not result.passed
        assert "160" in result.reason  # thumbnail gate fails first, in declared order


class TestPairwiseVerdict:
    def test_rejects_invalid_winner(self):
        with pytest.raises(ValueError, match="winner"):
            PairwiseVerdict(winner="c", model_id="m", provider_slug="p")


class TestAggregatePanel:
    def test_below_quorum_returns_no_quorum(self):
        verdicts = [PairwiseVerdict(winner="a", model_id="m1", provider_slug="p1")]
        result = aggregate_panel(verdicts, quorum=3)
        assert result.winner == "no_quorum"
        assert not result.quorum_met

    def test_clear_majority_wins(self):
        verdicts = [
            PairwiseVerdict(winner="a", model_id="m1", provider_slug="p1"),
            PairwiseVerdict(winner="a", model_id="m2", provider_slug="p2"),
            PairwiseVerdict(winner="b", model_id="m3", provider_slug="p3"),
        ]
        result = aggregate_panel(verdicts, quorum=3)
        assert result.winner == "a"
        assert result.quorum_met

    def test_split_panel_reports_tie(self):
        verdicts = [
            PairwiseVerdict(winner="a", model_id="m1", provider_slug="p1"),
            PairwiseVerdict(winner="b", model_id="m2", provider_slug="p2"),
            PairwiseVerdict(winner="tie", model_id="m3", provider_slug="p3"),
        ]
        result = aggregate_panel(verdicts, quorum=3)
        assert result.winner == "tie"

    def test_repeated_votes_from_one_judge_do_not_satisfy_quorum(self):
        """Quorum counts DISTINCT judges. Three samples from one model must NOT pass a
        quorum of 3 — that is the single-model-consensus failure the cross-vendor panel
        exists to prevent (BUILD_PLAN.md §3.19: 'not three copies of one prompt')."""
        verdicts = [
            PairwiseVerdict(winner="a", model_id="same-model", provider_slug="p1"),
            PairwiseVerdict(winner="a", model_id="same-model", provider_slug="p1"),
            PairwiseVerdict(winner="a", model_id="same-model", provider_slug="p1"),
        ]
        result = aggregate_panel(verdicts, quorum=3)
        assert result.winner == "no_quorum"
        assert not result.quorum_met

    def test_same_model_on_distinct_providers_counts_separately(self):
        """Judge identity is (model, provider), not model alone — the same model served
        by two providers is two endpoints and therefore two independent votes."""
        verdicts = [
            PairwiseVerdict(winner="a", model_id="m", provider_slug="p1"),
            PairwiseVerdict(winner="a", model_id="m", provider_slug="p2"),
            PairwiseVerdict(winner="b", model_id="m", provider_slug="p3"),
        ]
        result = aggregate_panel(verdicts, quorum=3)
        assert result.quorum_met
        assert result.winner == "a"

    def test_duplicate_voter_cannot_outweigh_a_distinct_one(self):
        """A judge that votes twice is counted once, so it cannot swing the result."""
        verdicts = [
            PairwiseVerdict(winner="a", model_id="dup", provider_slug="p1"),
            PairwiseVerdict(winner="a", model_id="dup", provider_slug="p1"),
            PairwiseVerdict(winner="b", model_id="m2", provider_slug="p2"),
            PairwiseVerdict(winner="b", model_id="m3", provider_slug="p3"),
        ]
        result = aggregate_panel(verdicts, quorum=3)
        assert result.quorum_met
        assert result.winner == "b"  # 1x'a' after dedup vs 2x'b'
