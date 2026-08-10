"""Bleed has to survive three stages agreeing on one number.

`design-compile` grows the CSS page box by the profile's bleed, `finish` insets
the PDF TrimBox by the same figure, and `preflight` measures what came out. If
any one of them uses a different number the press file is wrong in a way that
only shows up on a cutting machine, so the number's provenance -- the vendor
profile, and nothing else -- is what these assert.
"""

from __future__ import annotations

import re

import pytest

from profiles import load_profile
from stages.design_compile_stage import _emit_css, _default_designspec
from templates import TEMPLATES


def _page_size_mm(css: str) -> tuple[float, float]:
    m = re.search(r"@page \{\s*size:\s*([\d.]+)mm\s+([\d.]+)mm;", css)
    assert m, f"no @page size rule in:\n{css[:400]}"
    return float(m.group(1)), float(m.group(2))


def _bleed_mm(css: str) -> float | None:
    m = re.search(r"^\s*bleed:\s*([\d.]+)mm;", css, re.MULTILINE)
    return float(m.group(1)) if m else None


def _first_margin_mm(css: str, side: str) -> float:
    m = re.search(rf"margin-{side}:\s*([\d.]+)mm;", css)
    assert m, f"no margin-{side} in:\n{css[:400]}"
    return float(m.group(1))


def test_zero_bleed_declares_no_bleed_at_all():
    spec = {**_default_designspec(),
            "trimSize": {"width": 170.0, "height": 240.0, "unit": "mm"}}
    css = _emit_css(spec, bleed_mm=0.0)
    assert _page_size_mm(css) == (170.0, 240.0)
    assert _bleed_mm(css) is None


def test_bleed_is_declared_to_the_renderer_not_folded_into_the_page_size():
    """`size` must stay at trim.

    Adding the bleed to `size` by hand lays out the right sheet but leaves
    TrimBox == BleedBox == MediaBox in the PDF, and Ghostscript then refuses
    PDF/X. The renderer has to be told it is bleed.
    """
    spec = {**_default_designspec(),
            "trimSize": {"width": 170.0, "height": 240.0, "unit": "mm"}}
    css = _emit_css(spec, bleed_mm=3.0)
    assert _page_size_mm(css) == (170.0, 240.0)
    assert _bleed_mm(css) == 3.0


def test_bleed_does_not_move_the_type_area():
    """The page box stays at trim, so margins are measured from the trim edge
    and every line lands where it did without bleed."""
    spec = {**_default_designspec(),
            "trimSize": {"width": 170.0, "height": 240.0, "unit": "mm"},
            "margins": {"top": 20, "bottom": 22, "inside": 18, "outside": 22}}

    at_trim = _emit_css(spec, bleed_mm=0.0)
    with_bleed = _emit_css(spec, bleed_mm=3.0)

    for side in ("top", "bottom", "left", "right"):
        assert _first_margin_mm(with_bleed, side) == pytest.approx(
            _first_margin_mm(at_trim, side)
        )


# --- the Greek presets --------------------------------------------------

GREEK_SIZES = {
    "Greek 17x24": (170.0, 240.0),
    "Greek 14x21": (140.0, 210.0),
    "Greek 12x17": (120.0, 170.0),
    "Greek 21x29": (210.0, 290.0),
}


@pytest.mark.parametrize("name,size", GREEK_SIZES.items())
def test_greek_profile_declares_its_trim_and_3mm_bleed(name, size):
    profile = load_profile(name)
    assert profile is not None, f"{name} is not loadable from profiles/"
    assert (profile["trimSize"]["width"], profile["trimSize"]["height"]) == size
    assert profile["bleed"]["all"] == 3.0


@pytest.mark.parametrize("name,size", GREEK_SIZES.items())
def test_greek_template_matches_its_profile_and_sets_5mm_leading(name, size):
    """14.173pt is 5.00mm. The baseline grid must use the same figure as the
    leading, or the grid it claims to be on is not the grid the text sits on."""
    template = next(t for t in TEMPLATES.values() if t["name"] == name)
    assert (template["trimSize"]["width"], template["trimSize"]["height"]) == size
    assert template["typography"]["leading"] == 14.173
    assert template["grid"]["baselineIncrement"] == 14.173
    assert template["typography"]["leading"] / 72 * 25.4 == pytest.approx(5.0, abs=0.001)


def test_profile_trim_overrides_the_designspec_trim():
    """The two used to be declared independently and never reconciled: the spec
    said 152x229 while the profile said 152.4x228.6, and only check_trim_size's
    0.5mm tolerance kept the disagreement from failing every build."""
    spec = {**_default_designspec(),
            "trimSize": {"width": 152.0, "height": 229.0, "unit": "mm"}}
    profile = load_profile("Greek 12x17")
    merged = {**spec, "trimSize": profile["trimSize"]}
    assert _page_size_mm(_emit_css(merged, bleed_mm=0.0)) == (120.0, 170.0)
