"""
Running heads and folios come from the DesignSpec, and both engines agree.

Before this, the two emitters each carried their own idea of what a running head
is: the CSS emitter wrote `font-size: 9pt` three times over, and the Typst
emitter wrote `max(7.0, body_size - 1.5)`. Neither consulted the DesignSpec, so
the same book rendered with different page furniture depending on the engine,
and a house rule like "running heads are body minus three, bold, all caps,
tracked 100" could not be expressed anywhere at all.

The cross-engine test is the one that matters. Per-engine assertions catch a
broken emitter; only comparing them catches the two drifting apart again.
"""

from __future__ import annotations

import pytest

from stages.design_compile_stage import _default_designspec, _emit_css
from stages.typst_stages import _emit_typst
from templates import DEFAULT_LEADING_PT, TEMPLATES

BODY_SIZE = 10.5


def _spec(**overrides) -> dict:
    spec = _default_designspec()
    spec["typography"]["bodySize"] = BODY_SIZE
    for block, values in overrides.items():
        spec.setdefault(block, {}).update(values)
    return spec


def _css_block(css: str, needle: str) -> str:
    """The declarations of the margin box whose content matches `needle`."""
    start = css.index(needle)
    return css[start:css.index("}", start)]


def test_running_head_size_is_body_minus_three(tmp_path):
    css = _emit_css(_spec())
    head = _css_block(css, "string(recto-head)")

    assert f"font-size: {BODY_SIZE - 3:g}pt;" in head
    assert "font-weight: 700;" in head
    assert "text-transform: uppercase;" in head
    # InDesign tracking 100 = 100/1000 em. Authored in InDesign units because
    # InDesign is one of the deliverables.
    assert "letter-spacing: 0.1em;" in head


def test_folio_size_is_body_minus_one(tmp_path):
    css = _emit_css(_spec())
    folio = _css_block(css, "counter(page,")

    assert f"font-size: {BODY_SIZE - 1:g}pt;" in folio
    assert "font-weight: 400;" in folio
    assert "text-transform" not in folio


def test_both_engines_read_the_same_spec():
    """The anti-drift check. CSS said 9pt, Typst said body-1.5; both ignored the
    spec, so the same manuscript got different running heads per engine."""
    spec = _spec()
    css, typ = _emit_css(spec), _emit_typst(spec)

    head_size = f"{BODY_SIZE - 3:g}pt"
    folio_size = f"{BODY_SIZE - 1:g}pt"

    assert f"font-size: {head_size};" in _css_block(css, "string(recto-head)")
    assert f"size: {head_size}" in typ
    assert f"font-size: {folio_size};" in _css_block(css, "counter(page,")
    assert f"size: {folio_size}" in typ

    # Case and tracking too, in each engine's own spelling.
    assert "upper(" in typ and "tracking: 0.1em" in typ
    assert 'weight: "bold"' in typ


def test_changing_the_spec_moves_both_engines():
    """A delta, not an absolute size: the rule is a relationship to body size,
    so it must still hold when body size changes."""
    spec = _spec(runningHeads={"sizeDelta": -2, "case": "small-caps",
                               "tracking": 0, "weight": "regular"})
    spec["typography"]["bodySize"] = 12.0

    css, typ = _emit_css(spec), _emit_typst(spec)
    head = _css_block(css, "string(recto-head)")

    assert "font-size: 10pt;" in head
    assert "font-variant-caps: small-caps;" in head
    assert "letter-spacing" not in head
    assert "size: 10pt" in typ and "smallcaps(" in typ
    assert "tracking:" not in typ.split("header:")[1].split("})")[0]


def test_outside_folios_alternate_by_page_parity():
    """`bottom-outside` used to be mapped to `center` in the Typst emitter, so a
    spec asking for outside folios silently got centred ones."""
    typ = _emit_typst(_spec(folio={"position": "bottom-outside"}))
    assert "calc.odd(here().page())" in typ


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_sits_on_the_house_baseline(name):
    """14.173pt = 5.000mm. Presets carried eight different leadings, none of
    them derived from anything, and a grid whose increment disagreed with the
    leading it was supposed to describe."""
    template = TEMPLATES[name]
    leading = template["typography"]["leading"]

    assert leading == DEFAULT_LEADING_PT
    assert leading / 72 * 25.4 == pytest.approx(5.0, abs=0.001)
    grid = template.get("grid") or {}
    if "baselineIncrement" in grid:
        assert grid["baselineIncrement"] == leading, (
            "the baseline grid must describe the leading the text actually sits on"
        )
