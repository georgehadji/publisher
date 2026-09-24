"""
Books are set in the face their design names, or not rendered at all.

Every template asked for EB Garamond; no machine this project ran on had it; the
renderer substituted whatever it found, and nothing noticed -- no book was ever
set in its designed face. Now `publisher_prepress.fontvault` resolves each family
to files, `paginate` pins and embeds exactly those, and a family that is not
installed fails the render.

The fonts here are built on the fly (fontTools' FontBuilder), so the tests do
not depend on what a machine happens to have installed -- the lesson of the
pagemap tests, where line breaks moved with the host's fonts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from publisher_prepress import fontvault
from publisher_stages import ErrorKind, StageCtx, StageError

FAMILY = "Publisher Test Serif"
LATIN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,"


def _make_font(path: Path, family: str, style: str, weight: int, italic: bool,
               *, typographic_family: str | None = None, width: int = 5) -> Path:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    glyphs = [".notdef", "space"] + [f"u{ord(c):04X}" for c in LATIN]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyphs)
    builder.setupCharacterMap({32: "space", **{ord(c): f"u{ord(c):04X}" for c in LATIN}})
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 600))
    pen.lineTo((450, 600))
    pen.closePath()
    box = pen.glyph()
    builder.setupGlyf({g: (TTGlyphPen(None).glyph() if g == "space" else box) for g in glyphs})
    builder.setupHorizontalMetrics({g: (500, 0) for g in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    names = {"familyName": family, "styleName": style}
    if typographic_family:
        names["typographicFamily"] = typographic_family
    builder.setupNameTable(names)
    builder.setupOS2(usWeightClass=weight, usWidthClass=width,
                     fsSelection=(1 if italic else 0) | (32 if weight == 700 else 0)
                     | (64 if not italic and weight == 400 else 0))
    builder.setupPost()
    builder.save(str(path))
    return path


@pytest.fixture
def font_dir(tmp_path, monkeypatch) -> Path:
    """A PUBLISHER_FONT_DIRS holding the four RIBBI faces of the test family."""
    root = tmp_path / "fonts"
    root.mkdir()
    for style, weight, italic in (("Regular", 400, False), ("Italic", 400, True),
                                  ("Bold", 700, False), ("Bold Italic", 700, True)):
        _make_font(root / f"test-{style.replace(' ', '')}.ttf", FAMILY, style, weight, italic)
    monkeypatch.setenv(fontvault.FONT_DIRS_ENV, str(root))
    fontvault._font_index.cache_clear()
    yield root
    fontvault._font_index.cache_clear()


# ── the vault's house faces ────────────────────────────────────────────


@pytest.mark.parametrize("family", ["GFS Didot", "Minion Pro", "PN Katsoulidis"])
def test_the_house_faces_are_licensed_for_print(family):
    for style in ("regular", "italic", "bold", "bold-italic"):
        fontvault.validate_font_use(family, style, "PRINT_PDF")


@pytest.mark.parametrize("family", ["Minion Pro", "PN Katsoulidis"])
def test_the_commercial_faces_are_not_licensed_for_epub(family):
    """Embedding a commercial face in an EPUB redistributes it."""
    with pytest.raises(fontvault.FontLicenseViolation):
        fontvault.validate_font_use(family, "regular", "EPUB_EMBED")


def test_the_default_design_is_set_in_gfs_didot():
    from stages.design_compile_stage import _default_designspec
    import templates

    assert _default_designspec()["typography"]["bodyFont"]["family"] == "GFS Didot"
    greek = [v for v in vars(templates).values()
             if isinstance(v, dict) and str(v.get("name", "")).startswith("Greek")]
    assert greek and all(t["typography"]["bodyFont"]["family"] == "GFS Didot" for t in greek)


# ── locating files ─────────────────────────────────────────────────────


def test_each_style_resolves_to_its_own_file(font_dir):
    for style, name in (("regular", "Regular"), ("italic", "Italic"),
                        ("bold", "Bold"), ("bold-italic", "BoldItalic")):
        assert fontvault.locate_font(FAMILY, style) == font_dir / f"test-{name}.ttf"


def test_a_wrong_typographic_family_does_not_steal_a_slot(font_dir):
    """GFS Artemisia's bold italic claims typographic family "GFS Didot" and,
    sorted first, won GFS Didot's bold-italic slot. Name ID 1 decides."""
    _make_font(font_dir / "a-impostor.ttf", "Other Family", "Bold Italic", 700, True,
               typographic_family=FAMILY)
    fontvault._font_index.cache_clear()
    assert fontvault.locate_font(FAMILY, "bold-italic") == font_dir / "test-BoldItalic.ttf"


def test_faces_the_vault_has_no_slot_for_are_ignored(tmp_path, monkeypatch):
    root = tmp_path / "only-odd"
    root.mkdir()
    _make_font(root / "medium.ttf", FAMILY, "Medium", 500, False)
    _make_font(root / "cond.ttf", FAMILY, "Regular", 400, False, width=3)
    monkeypatch.setenv(fontvault.FONT_DIRS_ENV, str(root))
    fontvault._font_index.cache_clear()
    try:
        assert fontvault.locate_font(FAMILY) is None
    finally:
        fontvault._font_index.cache_clear()


def test_font_faces_pins_found_families_and_names_missing_ones(font_dir):
    rules, missing = fontvault.font_faces([FAMILY, "No Such Face"])
    assert missing == ["No Such Face"]
    assert rules.count("@font-face") == 4
    assert (font_dir / "test-Regular.ttf").resolve().as_uri() in rules


# ── paginate ───────────────────────────────────────────────────────────


def _paginate(tmp_path: Path, family: str, text: str):
    pytest.importorskip("weasyprint")
    from stages.paginate_stage import paginate
    from stages.rendering import emit_css

    ast = {"schema": "ast/1", "metadata": {"title": "T"}, "frontMatter": [], "backMatter": [],
           "body": [{"type": "chapter", "attrs": {"number": 1, "title": "One", "id": "ch1"},
                     "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}]}
    spec = {"typography": {"bodyFont": {"family": family}, "headingFont": {"family": family}},
            "chapterOpenings": {"dropCap": False}}
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps(ast), encoding="utf-8")
    css = tmp_path / "book.css"
    css.write_text(emit_css(spec), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    ctx = StageCtx(build_id="fonts", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=256, work_dir=str(work), allow_stub_engines=False)
    return paginate(ctx, doc_path=str(doc), css_path=str(css))


def test_a_face_that_is_not_installed_fails_the_render(tmp_path, font_dir):
    with pytest.raises(StageError) as exc:
        _paginate(tmp_path, "No Such Face", "Some text.")
    assert exc.value.kind == ErrorKind.INFRA
    assert "No Such Face" in exc.value.message


def test_the_designed_face_is_the_one_embedded(tmp_path, font_dir, capsys):
    result = _paginate(tmp_path, FAMILY, "Set in the test face.")
    assert result.metrics["fallback_font_count"] == 0
    assert "fallback font" not in capsys.readouterr().out


def test_glyphs_the_face_lacks_are_reported_as_fallbacks(tmp_path, font_dir, capsys):
    """The test face has no Greek; the words still print, from another font."""
    result = _paginate(tmp_path, FAMILY, "Socrates, Σωκράτης.")
    assert result.metrics["fallback_font_count"] >= 1
    assert "fallback font" in capsys.readouterr().out
