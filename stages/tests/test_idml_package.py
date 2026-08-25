"""
The IDML deliverable -- package structure, geometry, and the stage itself.

These tests exist because the previous IDML tests asserted only that certain ZIP
entry names were present, and passed on a package InDesign rejects outright. The
assertions here are the properties that decide whether the file opens: mimetype
first and STORED, container -> designmap, every idPkg part resolving, every style
and story reference resolving.

Cross-checked against a real InDesign-authored IDML (DOMVersion 10.0), not
against this writer's own idea of the format.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from publisher_stages import ErrorKind, StageCtx, StageError
from publisher_idml import (
    IDMLValidationError, IDMLWriter, normalize_story_xml, validate_idml,
)

HAVE_PANDOC = bool(shutil.which("pandoc"))

STORY = """<ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/Header1">
  <CharacterStyleRange AppliedCharacterStyle="$ID/NormalCharacterStyle">
    <Content>The First</Content>
  </CharacterStyleRange>
</ParagraphStyleRange>
<Br />
<ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/Paragraph">
  <CharacterStyleRange AppliedCharacterStyle="CharacterStyle/Italic">
    <Content>Body text.</Content>
  </CharacterStyleRange>
</ParagraphStyleRange>"""

SPEC = {
    "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
    "typography": {"bodyFont": {"family": "EB Garamond"}, "bodySize": 10.5,
                   "leading": 14.0, "paragraphIndent": 1.5,
                   "bodyAlignment": "justified"},
    "margins": {"top": 18, "bottom": 20, "inside": 15, "outside": 20},
    "chapterOpenings": {"startsOn": "recto"},
}
PROFILE = {"trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
           "bleed": {"all": 3.0}}


def _write(tmp_path: Path, **kwargs) -> Path:
    return IDMLWriter(STORY, title="Test Book", designspec=SPEC,
                      profile=PROFILE, **kwargs).write(tmp_path / "book.idml")


def test_package_passes_structural_validation(tmp_path):
    summary = validate_idml(_write(tmp_path, page_count=4))
    assert summary["stories"] == 1
    assert summary["text_frames"] == 4


def test_mimetype_is_first_and_stored(tmp_path):
    """InDesign reads the media type before parsing any XML, exactly as EPUB and
    ODF readers do. Deflate it, or write it second, and the package is
    unrecognisable no matter how correct the rest is."""
    with zipfile.ZipFile(_write(tmp_path, page_count=1)) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/vnd.adobe.indesign-idml-package"


def test_frames_are_threaded_into_one_story(tmp_path):
    """A book is one story flowing through many frames. Unthreaded frames would
    show the same first page of text repeated and the rest overset."""
    with zipfile.ZipFile(_write(tmp_path, page_count=3)) as zf:
        spreads = [zf.read(f"Spreads/Spread_uspread{p}.xml").decode() for p in (1, 2, 3)]
    assert 'PreviousTextFrame="n"' in spreads[0]
    assert 'NextTextFrame="uframe2"' in spreads[0]
    assert 'PreviousTextFrame="uframe1"' in spreads[1]
    assert 'NextTextFrame="n"' in spreads[2]


def test_margins_mirror_by_page_parity(tmp_path):
    """The binding edge swaps sides between recto and verso. One fixed pair puts
    every gutter on the same side and binds half the book into the spine."""
    with zipfile.ZipFile(_write(tmp_path, page_count=2)) as zf:
        recto = zf.read("Spreads/Spread_uspread1.xml").decode()
        verso = zf.read("Spreads/Spread_uspread2.xml").decode()
    inside_pt, outside_pt = 15 * 72 / 25.4, 20 * 72 / 25.4
    assert f'Left="{inside_pt:.6f}"' in recto and f'Right="{outside_pt:.6f}"' in recto
    assert f'Left="{outside_pt:.6f}"' in verso and f'Right="{inside_pt:.6f}"' in verso


def test_page_box_and_bleed_come_from_the_profile(tmp_path):
    with zipfile.ZipFile(_write(tmp_path, page_count=1)) as zf:
        prefs = zf.read("Resources/Preferences.xml").decode()
    assert f'PageWidth="{152.4 * 72 / 25.4:.6f}"' in prefs
    assert f'DocumentBleedTopOffset="{3.0 * 72 / 25.4:.6f}"' in prefs
    # The load-bearing preference: InDesign fixes our frame-count guess on open.
    assert 'SmartTextReflow="true"' in prefs and 'AddPages="EndOfStory"' in prefs


def test_chapters_open_recto_via_the_style(tmp_path):
    with zipfile.ZipFile(_write(tmp_path, page_count=1)) as zf:
        styles = zf.read("Resources/Styles.xml").decode()
    assert 'StartParagraph="NextOddPage"' in styles


def test_unknown_styles_are_repointed_not_left_dangling():
    """pandoc emits nested group styles ("Blockquote > Paragraph") and heading
    anchors this package does not define. Left alone they are references into
    nothing; the validator would (correctly) reject the package."""
    raw = ('<ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/Blockquote &gt; Paragraph">'
           '<CharacterStyleRange AppliedCharacterStyle="CharacterStyle/Subscript">'
           '<HyperlinkTextDestination Self="HyperlinkTextDestination/#x" Name="D" />'
           "<Content>q</Content></CharacterStyleRange></ParagraphStyleRange>")
    out = normalize_story_xml(raw)
    assert "HyperlinkTextDestination" not in out
    assert 'AppliedParagraphStyle="ParagraphStyle/Paragraph"' in out
    assert 'AppliedCharacterStyle="$ID/NormalCharacterStyle"' in out


def test_validator_rejects_malformed_xml(tmp_path):
    """The parts are assembled as text. One unescaped character and InDesign's
    parser stops -- a failure no structural regex check can see."""
    bad = tmp_path / "broken.idml"
    good = _write(tmp_path, page_count=1)
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "Resources/Styles.xml":
                data = data.replace(b"</idPkg:Styles>", b"<Unclosed>")
            dst.writestr(info, data, compress_type=info.compress_type)
    with pytest.raises(IDMLValidationError, match="well-formed"):
        validate_idml(bad)


def test_validator_rejects_a_package_with_a_deflated_mimetype(tmp_path):
    """Guards the guard: a validator that cannot fail is not a gate."""
    bad = tmp_path / "bad.idml"
    good = _write(tmp_path, page_count=1)
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))   # mimetype now deflated
    with pytest.raises(IDMLValidationError):
        validate_idml(bad)


def test_scribus_opens_the_package(tmp_path):
    """An independent implementation's opinion.

    `validate_idml` checks the package against the format; this checks it
    against a program. Scribus's importer is not InDesign's, so a pass is
    evidence rather than proof -- but a file Scribus cannot open at all is not
    one to hand a designer.

    Skipped unless Scribus is installed: it is a development tool, not part of
    the worker toolchain.
    """
    from publisher_idml.scribus_check import find_scribus, scribus_opens

    if find_scribus() is None:
        pytest.skip("Scribus is not installed (set PUBLISHER_SCRIBUS_BIN to point at it)")

    verdict = scribus_opens(_write(tmp_path, page_count=3))
    assert verdict.opened, f"Scribus refused the package:\n{verdict.output}"
    assert verdict.pages >= 1


@pytest.mark.skipif(not HAVE_PANDOC, reason="pandoc is not installed")
def test_stage_emits_a_valid_package_from_a_real_document(tmp_path):
    from stages.idml_stage import idml

    doc = {
        "schema": "doc-effective/1",
        "metadata": {"title": "Stage Book"},
        "frontMatter": [],
        "body": [{
            "type": "chapter",
            "attrs": {"id": "ch1", "number": 1, "title": "One"},
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": "Words. " * 50}]}],
        }],
        "backMatter": [],
    }
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    pagemap_path = tmp_path / "pagemap.json"
    pagemap_path.write_text(json.dumps({"pages": [{"pageNumber": i} for i in range(1, 6)]}),
                            encoding="utf-8")

    ctx = StageCtx(
        build_id="test-idml",
        cache_key="test-idml",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=256,
        work_dir=str(tmp_path),
        cas_root=str(tmp_path / "cas"),
    )
    result = idml(ctx, doc_path=str(doc_path), pagemap_path=str(pagemap_path))

    art = result.artifacts[0]
    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    # Validated as delivered bytes, from CAS -- not the temp file the writer made.
    validate_idml(stored)
    assert result.metrics["page_hint"] == 5.0
    assert result.metrics["frame_count"] == 5.0

    with zipfile.ZipFile(stored) as zf:
        story = zf.read("Stories/Story_ustory.xml").decode()
    assert "One" in story and "Words." in story


def test_stage_refuses_a_document_that_never_reached_resolve(tmp_path):
    from stages.idml_stage import idml

    ctx = StageCtx(
        build_id="test-idml",
        cache_key="test-idml",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=256,
        work_dir=str(tmp_path),
        cas_root=str(tmp_path / "cas"),
    )
    with pytest.raises(StageError) as exc:
        idml(ctx, doc_path=None)
    assert exc.value.kind == ErrorKind.BAD_INPUT
