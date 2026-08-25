"""
The Typst render path -- geometry, DAG selection, and (when the toolchain is
installed) a real pandoc + Typst render.

The first two tests run everywhere. The third is the one that matters and is
skipped without pandoc/typst on PATH; that is deliberate -- the stage itself
refuses to stub, so a machine without the binaries has nothing to assert about
a render that cannot happen.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from publisher_stages import StageCtx, get_registry
import stages  # noqa: F401 -- registers every stage
from stages.typst_stages import _emit_typst, paginate_typst

HAVE_TOOLCHAIN = bool(shutil.which("pandoc") and shutil.which("typst"))

DOC = {
    "schema": "doc-effective/1",
    "metadata": {"title": "Test Book"},
    "frontMatter": [],
    "body": [
        {
            "type": "chapter",
            "attrs": {"id": "ch1", "number": 1, "title": "The First"},
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": "Alpha. " * 400}]}],
        },
        {
            "type": "chapter",
            "attrs": {"id": "ch2", "number": 2, "title": "The Second"},
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": "Beta. " * 400}]}],
        },
    ],
    "backMatter": [],
}

SPEC = {
    "preferredEngine": "typst",
    "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
    "typography": {"bodyFont": {"family": "Liberation Serif"}, "bodySize": 10.5,
                   "leading": 14.0, "paragraphIndent": 1.5,
                   "bodyAlignment": "justified"},
    "margins": {"top": 18, "bottom": 20, "inside": 15, "outside": 20},
    "folio": {"position": "bottom-center", "style": "arabic"},
    "runningHeads": {"versoSource": "book-title"},
    "chapterOpenings": {"startsOn": "recto", "titleTreatment": "centered"},
    "colors": {"text": "#000000", "paper": "#FFFFFF"},
}


def test_bleed_grows_page_box_and_margins():
    """Typst writes no TrimBox, so `finish-gs` insets one from the MediaBox --
    which only works if the renderer laid out at trim + 2*bleed with margins
    grown to match. A page emitted at exactly trim cannot carry bleed at all,
    and preflight is right to fail it."""
    styles = _emit_typst(SPEC, bleed_mm=3.0)

    assert "width: 158.4mm" in styles      # 152.4 + 2*3
    assert "height: 234.6mm" in styles     # 228.6 + 2*3
    assert "top: 21mm" in styles           # 18 + 3
    assert "outside: 23mm" in styles       # 20 + 3
    # Type size 10.5 with 14.0 baseline-to-baseline is a 3.5pt Typst leading.
    assert "leading: 3.5pt" in styles
    assert 'pagebreak(to: "odd"' in styles

    at_trim = _emit_typst(SPEC, bleed_mm=0.0)
    assert "width: 152.4mm" in at_trim
    assert "top: 18mm" in at_trim


def test_typst_selection_keeps_the_dag_clean():
    """Selecting the Typst path must bind exactly one producer of raw-pdf/1 and
    leave no unsatisfiable input -- the CSS emitter and weasyprint renderer go
    inactive, they do not co-exist."""
    registry = get_registry()
    try:
        registry.select_implementation("design-compile", "design-compile-typst")
        registry.select_implementation("paginate", "paginate-typst")

        errors = [v for v in registry.check_integrity() if v["severity"] == "error"]
        assert errors == [], errors

        dag = registry.derive_dag()
        assert "paginate-typst" in dag["finish-gs"]
        assert "paginate" not in dag["finish-gs"]
        assert "design-compile-typst" in dag["paginate-typst"]
    finally:
        registry.select_implementation("design-compile", "design-compile")
        registry.select_implementation("paginate", "paginate")


@pytest.mark.skipif(not HAVE_TOOLCHAIN, reason="pandoc and typst are not installed")
def test_renders_a_real_pdf_with_measured_chapter_pages():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        doc_path = tmp_path / "doc.json"
        doc_path.write_text(json.dumps(DOC), encoding="utf-8")
        typ_path = tmp_path / "styles.typ"
        typ_path.write_text(_emit_typst(SPEC, bleed_mm=3.0), encoding="utf-8")

        ctx = StageCtx(
            build_id="test-typst",
            cache_key="test-typst",
            deadline=datetime.now(timezone.utc),
            memory_budget_mb=512,
            work_dir=str(tmp_path),
            cas_root=str(tmp_path / "cas"),
        )
        result = paginate_typst(ctx, doc_path=str(doc_path), typ_path=str(typ_path))

        pdf = next(a for a in result.artifacts if a.kind == "pdf")
        assert (Path(ctx.cas_root) / pdf.hash[:2] / pdf.hash[2:4] / pdf.hash
                ).read_bytes()[:4] == b"%PDF"

        assert result.metrics["page_count_measured"] == 1.0
        assert result.metrics["page_count"] >= 2
        # Both chapters must be LOCATED, not apportioned: the pagemap is what a
        # spine width is ultimately priced off.
        assert result.metrics["chapters_located"] == 2.0

        pm = next(a for a in result.artifacts if a.kind == "pagemap")
        pagemap = json.loads(
            (Path(ctx.cas_root) / pm.hash[:2] / pm.hash[2:4] / pm.hash).read_bytes())
        ch2 = next(c for c in pagemap["chapters"] if c["chapterId"] == "ch2")
        assert ch2["startPage"] > 1
        assert ch2["startPage"] % 2 == 1, "chapters open recto per the DesignSpec"
