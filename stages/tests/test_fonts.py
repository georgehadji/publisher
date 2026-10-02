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
    from templates import house_designspec
    import templates

    assert house_designspec()["typography"]["bodyFont"]["family"] == "GFS Didot"
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


# ── the cache key sees the font files (W7) ─────────────────────────────

def test_the_fontset_hash_changes_with_a_fonts_bytes(font_dir):
    before = fontvault.fontset_hash([FAMILY])
    assert fontvault.fontset_hash([FAMILY]) == before            # stable
    _make_font(font_dir / "test-Regular.ttf", FAMILY, "Regular", 400, False, width=5)
    path = font_dir / "test-Regular.ttf"
    path.write_bytes(path.read_bytes() + b"\0")                  # same face, other bytes
    assert fontvault.fontset_hash([FAMILY]) != before


def test_installing_a_missing_family_changes_the_hash(font_dir):
    assert fontvault.fontset_hash([FAMILY, "Not Yet"]) != fontvault.fontset_hash([FAMILY])


def test_paginates_cache_key_includes_its_font_files(font_dir, tmp_path):
    import stages  # noqa: F401
    from publisher_exec import _cache_key_for
    from publisher_stages import get_registry
    decl = get_registry().get("paginate")
    css = tmp_path / "book.css"
    css.write_text(f'body {{ font-family: "{FAMILY}"; }}', encoding="utf-8")
    inputs = {"doc_path": str(tmp_path / "doc.json"), "css_path": str(css)}
    before = _cache_key_for("paginate", decl, inputs)
    path = font_dir / "test-Bold.ttf"
    path.write_bytes(path.read_bytes() + b"\0")
    assert _cache_key_for("paginate", decl, inputs) != before


def test_the_manifest_carries_file_hashes_where_files_exist(font_dir, monkeypatch):
    monkeypatch.setitem(fontvault._BUILTIN_FONTS, f"{FAMILY}/regular", fontvault.FontAsset(
        family=FAMILY, style="regular", hash="unverified:x", source="bundled_ofl", licenseRef="OFL-1.1"))
    manifest = fontvault.build_font_manifest([(FAMILY, "regular")], "b1")
    assert manifest.fonts[0].hash == fontvault.hash_font_file(font_dir / "test-Regular.ttf")


# ── W8: every template's faces, Typst and EPUB through the vault ────────

def _all_designs():
    from templates import TEMPLATES, house_designspec
    return {"house": house_designspec(), **TEMPLATES}


@pytest.mark.parametrize("name", sorted(_all_designs()))
def test_every_template_names_only_faces_the_vault_licenses(name):
    """A template naming a face the vault does not know renders in a
    substitute face, or not at all: design-compile refuses it."""
    for family, style in fontvault.fonts_in_spec(_all_designs()[name]):
        fontvault.validate_font_use(family, style, "PRINT_PDF")


def _vault_entry(monkeypatch, uses: list[str]) -> None:
    for style in fontvault.FACE_CSS:
        monkeypatch.setitem(fontvault._BUILTIN_FONTS, f"{FAMILY}/{style}", fontvault.FontAsset(
            family=FAMILY, style=style, hash="test", source="bundled_ofl",
            licenseRef="OFL-1.1", allowedUses=uses))


def test_typst_is_given_exactly_the_vault_files(font_dir, tmp_path):
    from stages.typst_stages import _pin_fonts

    pinned = tmp_path / "pinned"
    args = _pin_fonts(f'#set text(font: ("{FAMILY}", "Liberation Serif"))', pinned)

    assert args == ["--font-path", str(pinned), "--ignore-system-fonts"]
    copied = {p.name.split("-", 1)[1]: p.read_bytes() for p in pinned.iterdir()}
    for face in font_dir.iterdir():
        assert copied[face.name] == face.read_bytes()


def test_typst_refuses_a_face_with_no_file(font_dir, tmp_path):
    from stages.typst_stages import _pin_fonts

    with pytest.raises(StageError) as exc:
        _pin_fonts('#set text(font: ("No Such Face", "Liberation Serif"))', tmp_path / "pinned")
    assert exc.value.kind == ErrorKind.INFRA
    assert "No Such Face" in str(exc.value.message)


def test_typst_preamble_faces_and_fallbacks():
    from stages.typst_stages import _emit_typst, _typst_fonts
    from templates import house_designspec

    styles = _emit_typst({**house_designspec(), "preferredEngine": "typst"})
    assert _typst_fonts(styles) == (["GFS Didot"], ["Liberation Serif"])


def _epub_of(tmp_path: Path) -> tuple:
    import json
    import zipfile
    from stages.secondary_output_stages import epub

    doc = {"schema": "doc-effective/1", "metadata": {"title": "T", "language": "en"},
           "frontMatter": [], "backMatter": [],
           "body": [{"type": "chapter", "attrs": {"id": "ch1", "number": 1, "title": "One"},
                     "content": [{"type": "paragraph",
                                  "content": [{"type": "text", "text": "Words."}]}]}]}
    (tmp_path / "doc.json").write_text(json.dumps(doc), encoding="utf-8")
    spec = {"typography": {"bodyFont": {"family": FAMILY}}}
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    ctx = StageCtx(build_id="t", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_path / "work"),
                   cas_root=str(tmp_path / "cas"))
    result = epub(ctx, doc_path=str(tmp_path / "doc.json"),
                  designspec_path=str(tmp_path / "spec.json"))
    h = result.artifacts[0].hash
    zf = zipfile.ZipFile(Path(ctx.cas_root) / h[:2] / h[2:4] / h)
    return result, zf


def test_epub_embeds_a_face_licensed_for_it(font_dir, tmp_path, monkeypatch):
    _vault_entry(monkeypatch, ["PRINT_PDF", "EPUB_EMBED"])
    result, zf = _epub_of(tmp_path)
    with zf:
        fonts = sorted(zf.read(n) for n in zf.namelist() if n.startswith("OEBPS/fonts/"))
        css = zf.read("OEBPS/book.css").decode()
        opf = zf.read("OEBPS/content.opf").decode()

    assert fonts == sorted(f.read_bytes() for f in font_dir.iterdir())
    assert css.count("@font-face") == 4
    assert f'body {{ font-family: "{FAMILY}", serif; }}' in css
    assert opf.count('media-type="font/ttf"') == 4
    assert result.metrics["fonts_embedded"] == 4.0
    assert result.warnings == []


def test_epub_never_carries_a_print_only_face(font_dir, tmp_path, monkeypatch):
    """The commercial faces are licensed for print only: an EPUB is a zip any
    reader can open, so embedding one there redistributes it."""
    _vault_entry(monkeypatch, ["PRINT_PDF"])
    result, zf = _epub_of(tmp_path)
    with zf:
        assert not [n for n in zf.namelist() if n.startswith("OEBPS/fonts/")]
        assert "@font-face" not in zf.read("OEBPS/book.css").decode()
    assert [w.code for w in result.warnings] == ["epub-font-not-embedded"]
