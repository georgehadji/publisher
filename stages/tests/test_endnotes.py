"""
Endnotes (W9): printed where their chapter ends, in print, EPUB and Typst.

Word's endnotes used to become footnotes. Now they are `endnote` nodes, placed
like a footnote (after the paragraph citing them), and every renderer collects
them to the end of the chapter. The text stream (`ast_text`) defers them to the
same place -- the tests below hold both integrity checks to that.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from publisher_stages import StageCtx
from publisher_structure.rules import extract_text_from_html, normalize_text
from stages.rendering import ast_to_html
from stages.text_stream import ast_text


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _note(kind: str, text: str) -> dict:
    return {"type": kind, "attrs": {"number": 1}, "content": [{"type": "text", "text": text}]}


DOC = {
    "schema": "doc-effective/1",
    "metadata": {"title": "Notes", "language": "en"},
    "frontMatter": [],
    "body": [
        {"type": "chapter", "attrs": {"id": "ch1", "number": 1, "title": "One"},
         "content": [_para("First words."), _note("endnote", "Endnote one."),
                     _para("Second words."), _note("footnote", "A footnote.")]},
        {"type": "chapter", "attrs": {"id": "ch2", "number": 2, "title": "Two"},
         "content": [_para("Next chapter."), _note("endnote", "Endnote two.")]},
    ],
    "backMatter": [],
}


def test_print_sets_endnotes_at_their_chapters_end():
    html = ast_to_html(DOC)
    one, two = html.index('id="ch1"'), html.index('id="ch2"')

    assert one < html.index("Second words.") < html.index("Endnote one.") < two
    assert html.index("Next chapter.") < html.index("Endnote two.")
    # Numbered through the book, as the footnotes are; the numbers are CSS's.
    assert '<span class="endnote-call" data-n="1"></span>' in html[one:two]
    assert '<p class="endnote" data-n="2">' in html[two:]


def test_the_print_gate_reads_endnotes_where_they_print():
    """ast-assemble's comparison, on a book with endnotes. Read where they are
    cited, the source side would diverge at the first one."""
    html_side = normalize_text(extract_text_from_html(ast_to_html(DOC)))
    assert html_side == normalize_text(ast_text(DOC))
    assert html_side.index("Second words") < html_side.index("Endnote one")


def test_the_epub_lists_endnotes_after_its_chapter(tmp_path):
    from stages.secondary_output_stages import epub

    (tmp_path / "doc.json").write_text(json.dumps(DOC), encoding="utf-8")
    ctx = StageCtx(build_id="t", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_path / "work"),
                   cas_root=str(tmp_path / "cas"))
    # The stage itself fails unless the stored package's text equals ast_text.
    result = epub(ctx, doc_path=str(tmp_path / "doc.json"))

    h = result.artifacts[0].hash
    with zipfile.ZipFile(Path(ctx.cas_root) / h[:2] / h[2:4] / h) as zf:
        chapter = next(zf.read(n).decode() for n in zf.namelist()
                       if n.endswith(".xhtml") and "First words." in zf.read(n).decode())
    assert 'href="#en-1"' in chapter
    assert re.search(r'<li epub:type="endnote" id="en-1" value="1"><p>Endnote one\.', chapter)
    assert chapter.index("Second words.") < chapter.index("Endnote one.")


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc is not installed")
def test_typst_writes_the_endnote_numbers_css_would_draw(tmp_path):
    from stages.typst_stages import FOOTNOTE_FILTER_LUA, _build_main_typ, _chapters_of

    lua = tmp_path / "notes.lua"
    lua.write_text(FOOTNOTE_FILTER_LUA, encoding="utf-8")
    typ = _build_main_typ(DOC, "", _chapters_of(DOC), shutil.which("pandoc"), lua)

    assert "#super[1]" in typ and "#super[2]" in typ
    assert re.search(r"1\. Endnote one\.", typ)
    assert typ.index("Second words.") < typ.index("Endnote one.")
