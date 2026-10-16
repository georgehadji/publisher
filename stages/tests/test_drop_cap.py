"""
The drop cap on a chapter-opening paragraph. weasyprint 70 crashes (an
AssertionError in float layout) when it must push such a paragraph whole onto
the next page -- the house `orphans: 2`, with one line left -- if the drop cap is
a floated `::first-letter`: the pseudo-element's float is built inside the line
box (WeasyPrint #1859; the fix in 70.0 covers another path). A real element
floated by CSS is built in the right place, so print sets the first letter in
`<span class="drop-cap">`. A paragraph gets that role at a page foot by a
reviewer's `set_attr` on /attrs/role, a chapter merge, or an epigraph that fills
the page; ingest never sets it.
"""

from __future__ import annotations

import re

import pytest

from stages.rendering import _render_content, ast_to_epub_sections, emit_css


def _para(*runs, role="chapter-opening", fit=None, ref="p1") -> dict:
    attrs = {"role": role, **({"fit": fit} if fit else {})}
    return {"type": "paragraph", "sourceRef": {"docxId": ref}, "attrs": attrs,
            "content": [r if isinstance(r, dict) else {"type": "text", "text": r} for r in runs]}


def _text(html: str) -> str:
    """The text the integrity gate reads (publisher_structure.rules.parse_html)."""
    from publisher_structure.rules import parse_html
    return " ".join(b.text for b in parse_html(f'<section class="chapter">{html}</section>') if b.text)


def test_a_chapter_opening_sets_its_first_letter_in_a_drop_cap_span():
    html = _render_content([_para("The house stood at the end of a road.")])
    assert '<span class="drop-cap">T</span>he house' in html
    assert html.count("drop-cap") == 1


def test_only_a_chapter_opening_gets_one():
    assert "drop-cap" not in _render_content([_para("The house stood at the end of a road.", role="normal")])


def test_opening_punctuation_goes_with_the_letter_as_first_letter_took_it():
    assert '<span class="drop-cap">“T</span>he' in _render_content([_para("“The house,” she said, “stood there.”")])
    assert '<span class="drop-cap">«Τ</span>ο' in _render_content([_para("«Το σπίτι βρισκόταν στο τέλος.»")])


def test_a_combining_accent_stays_with_its_letter():
    assert '<span class="drop-cap">Ώ</span>ρα' in _render_content([_para("Ώρα να φύγουμε από εδώ.")])


def test_a_marked_first_run_keeps_its_mark_inside_the_span():
    html = _render_content([_para({"type": "text", "text": "Once", "marks": [{"type": "emphasis"}]},
                                  " upon a time there was a house.")])
    assert '<span class="drop-cap"><em>O</em></span><em>nce</em> upon' in html


def test_it_sits_inside_the_fit_span_and_beside_the_keep_span():
    html = _render_content([_para("Two words", fit="tight")])
    assert re.search(r'<span class="fit-tight"><span class="drop-cap">T</span><span class="keep">wo words</span></span>', html)


def test_the_integrity_gate_reads_the_same_text():
    for text in ("The house stood at the end of a road.", "“The house,” she said.", "Ώρα να φύγουμε."):
        assert _text(_render_content([_para(text)])) == _text(_render_content([_para(text, role="normal")]))


def test_the_stylesheet_floats_the_span_not_a_pseudo_element():
    css = emit_css({})
    assert "::first-letter" not in css
    rule = re.search(r"\.drop-cap \{([^}]*)\}", css).group(1)
    assert "float: left" in rule


def test_the_epub_sets_no_drop_cap():
    book = {"schema": "ast/1", "body": [{"type": "chapter", "attrs": {"number": 1, "id": "c1", "title": "One"},
                                         "content": [_para("The house stood at the end of a road.")]}]}
    assert "drop-cap" not in ast_to_epub_sections(book)[0]["body"]


def test_a_chapter_opening_pushed_to_the_next_page_renders():
    """The crash itself, in a real render. One-line paragraphs fill the page first:
    the leading is in points, so how many fit does not depend on the face, and the
    sweep puts the opening paragraph at the foot of a page on any of them."""
    weasyprint = pytest.importorskip("weasyprint")
    from publisher_prepress.fontvault import font_faces
    from stages.paginate_stage import PAGE_TEMPLATE, requested_font_families
    from stages.rendering import ast_to_html
    from templates import house_designspec

    css = emit_css(house_designspec())
    faces, _ = font_faces(requested_font_families(css))
    opening = " ".join(["the quick brown fox jumps over a lazy dog"] * 7)
    pushed = 0
    for lines in range(20, 46):
        book = {"schema": "ast/1", "metadata": {"title": "T", "language": "en"}, "body": [
            {"type": "chapter", "attrs": {"number": 1, "id": "c1", "title": "One"}, "content": [
                *(_para("Line.", role="normal", ref=f"l{i}") for i in range(lines)), _para(opening, ref="open")]}]}
        pages = weasyprint.HTML(string=PAGE_TEMPLATE.format(css=faces + css, html=ast_to_html(book))).render().pages
        pushed += len(pages) > 1
    assert pushed, "the sweep never reached a second page: widen it"
