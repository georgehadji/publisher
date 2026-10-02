"""
Inside margin and spine caliper (W6, docs/WIRING_PLAN.md).
"""

from __future__ import annotations

import pytest

from publisher_prepress.geometry import PT_PER_MM
from publisher_prepress.margins import inside_margins, required_inside_margin
from publisher_prepress.preflight import check_inside_margin


def _page(*words: tuple[float, float]) -> str:
    body = "".join(f'<word xMin="{a}" yMin="10.0" xMax="{b}" yMax="20.0">w</word>' for a, b in words)
    return f'<page width="420.0" height="600.0">{body}</page>'


# Trim inset 9 pt from the media box on each side (3.175 mm bleed is ~9 pt).
TRIM_LEFT, TRIM_RIGHT = 9.0, 411.0


def test_odd_pages_bind_left_even_pages_bind_right():
    html = _page((60.0, 300.0), (80.0, 350.0)) + _page((50.0, 340.0)) + _page()
    margins = inside_margins(html, TRIM_LEFT, TRIM_RIGHT)
    assert margins == {1: round(51 / PT_PER_MM, 2), 2: round(71 / PT_PER_MM, 2)}   # page 3 is blank


KDP = [{"maxPages": 150, "mm": 9.525}, {"maxPages": 300, "mm": 12.7}, {"maxPages": 828, "mm": 22.225}]


@pytest.mark.parametrize("pages, mm", [(24, 9.525), (150, 9.525), (151, 12.7), (300, 12.7), (301, 22.225), (900, 22.225)])
def test_the_band_is_the_first_that_covers_the_page_count(pages, mm):
    assert required_inside_margin(KDP, pages) == mm


def _info(margins, pages=200):
    return {"page_count": pages, "margins": margins}


def test_a_page_inside_the_minimum_fails_and_names_it():
    c = check_inside_margin(_info({"margins": {1: 15.0, 2: 11.0, 3: 12.0}}), {"bindingSpec": {"minInsideMarginMm": KDP}})
    assert c.status == "fail" and c.value == 11.0 and c.expected == 12.7 and c.sourceRef == "pdf#page=2"


def test_margins_at_or_over_the_minimum_pass():
    c = check_inside_margin(_info({"margins": {1: 12.7, 2: 20.0}}), {"bindingSpec": {"minInsideMarginMm": KDP}})
    assert c.status == "pass"


def test_unmeasured_is_a_warning_not_a_pass():
    c = check_inside_margin(_info({"unmeasured": "pdftotext (poppler-utils) is not installed"}),
                            {"bindingSpec": {"minInsideMarginMm": KDP}})
    assert c.status == "warn" and "not measured" in c.humanMessage


def test_a_profile_without_a_table_skips():
    assert check_inside_margin(_info({"margins": {1: 1.0}}), {}).status == "skip"


def test_kdp_states_its_caliper_and_bands_and_others_do_not():
    from profiles import list_profiles
    for profile in list_profiles():
        stated = "pageThicknessMm" in profile.get("coverSpec", {})
        assert stated == (profile["vendor"] == "kdp"), profile["name"]
        if profile["vendor"] == "kdp":
            assert profile["coverSpec"]["pageThicknessMm"] == 0.0572
            assert required_inside_margin(profile["bindingSpec"]["minInsideMarginMm"], 700) == 19.05


@pytest.mark.parametrize("profile_name, warns", [("KDP US Trade 6x9", False), ("IngramSpark US Trade 6x9", True)])
def test_the_cover_warns_when_the_spine_uses_the_default_caliper(tmp_path, profile_name, warns):
    from datetime import datetime, timezone
    from publisher_stages import StageCtx
    from stages.prepress_stages import cover_stage
    ctx = StageCtx(build_id="b", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_path), cas_root=str(tmp_path / "cas"))
    result = cover_stage(ctx, page_count=200, profile_name=profile_name)
    assert [w.code for w in result.warnings] == (["spine-caliper-default"] if warns else [])
    assert result.metrics["spine_width_mm"] == (12.0 if warns else round(200 * 0.0572, 2))
