"""
No runts: a paragraph's last line never holds a single word in print.

The first real book carried 264 runt pages (preflight warns on them). The print
renderer now sets each paragraph's last two words as one unbreakable unit
(`rendering.KEEP`, `<span class="keep">`, `white-space: nowrap`). These tests pin
that it changes no text -- the integrity gate compares characters -- and that on
a real render it removes the runts an unglued control produces.
"""

from __future__ import annotations

import re

import pytest

from stages.rendering import (KEEP_TAIL_CHARS, _render_content, _render_inline, ast_to_epub_sections,
                              ast_to_html, emit_css)


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_the_last_two_words_are_kept_together_and_no_character_changes():
    html = _render_content([_para("one two three four ")])
    assert '<span class="keep">three four</span>' in html
    assert _text(html) == "one two three four "


def test_marks_survive_and_a_pair_split_across_runs_is_kept_whole():
    content = [{"type": "text", "text": "plain "}, {"type": "text", "text": "in italic now",
                                                    "marks": [{"type": "emphasis"}]}]
    html = _render_content([{"type": "paragraph", "content": content}])
    assert '<span class="keep"><em>italic now</em></span>' in html
    assert _text(html) == "plain in italic now"
    # Word splits runs at every formatting boundary: the last word is often a run
    # of its own (107 of the first real book's paragraphs).
    split = [{"type": "text", "text": "it ends "}, {"type": "text", "text": "last.",
                                                    "marks": [{"type": "strong"}]}]
    html = _render_content([{"type": "paragraph", "content": split}])
    assert '<span class="keep">ends <strong>last.</strong></span>' in html
    assert _text(html) == "it ends last."


def test_any_whitespace_before_the_last_word_is_kept():
    html = _render_content([_para("cited (Bailey,  2021).")])
    assert '<span class="keep">(Bailey,  2021).</span>' in html


def test_a_single_short_word_is_left_alone():
    assert 'class="keep"' not in _render_content([_para("alone")])


def test_a_pair_too_long_for_the_cap_keeps_its_last_stretch_instead():
    """A URL breaks at its slashes: kept whole it could overflow the line, left
    alone its last fragment sat on a line by itself (11 of the first real
    book's last 24 runts)."""
    url = "https://example.org/digitalResources/ancient_greek/library/index.html?author_id=199."
    html = _render_content([_para("See " + url)])
    kept = re.search(r'<span class="keep">(.*?)</span>', html).group(1)
    assert kept == url[-(KEEP_TAIL_CHARS * 3 // 4):]
    assert _text(html) == "See " + url


def test_the_note_calls_stay_on_the_last_word_s_line():
    """Rendered after the text, a line could break before a call and leave the
    number alone on the paragraph's last line (5 runts in the first real book)."""
    html = _render_content([_para("The cited words end here. "),
                            {"type": "footnote", "content": [{"type": "text", "text": "One."}]},
                            {"type": "footnote", "content": [{"type": "text", "text": "Two."}]}])
    assert re.search(r'<span class="keep">end here\. '
                     r'<span class="note-call" data-n="1"></span>'
                     r'<span class="note-call" data-n="2"></span></span>', html), html
    assert _text(html) == "The cited words end here.  One. Two."


def test_headings_titles_and_cells_are_kept_but_not_the_epub():
    heading = {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "A long heading"}]}
    assert '<h2>A <span class="keep">long heading</span></h2>' in _render_content([heading])
    # Auto table layout widens a column to fit the pair; KEEP_CELL_CHARS bounds how far.
    table = {"type": "table", "content": [{"type": "tableRow", "content": [
        {"type": "tableCell", "content": [_para("two words")]}]}]}
    assert '<span class="keep">two words</span>' in _render_content([table])
    book = {"schema": "ast/1", "metadata": {"title": "T"}, "frontMatter": [], "backMatter": [],
            "body": [{"type": "chapter", "attrs": {"number": 1, "title": "The First Chapter", "id": "c1"},
                      "content": [_para("a b c d")]}]}
    assert '<h1 class="chapter-title">The <span class="keep">First Chapter</span></h1>' in ast_to_html(book)
    assert not any('class="keep"' in section["body"] for section in ast_to_epub_sections(book))


def test_the_notes_last_words_are_kept_too():
    html = _render_content([_para("Body text."),
                            {"type": "footnote", "content": [{"type": "text", "text": "See the note here."}]}])
    assert '<span class="keep">note here.</span>' in html


CSS = "@page{size:200pt 120pt;margin:8pt} p{font-size:9pt;margin:0;orphans:1;widows:1}"


def _runts(html: str) -> int:
    weasyprint = pytest.importorskip("weasyprint")
    from stages.paginate_stage import _measure_pages

    css = CSS + re.search(r"\.keep \{[^}]*\}", emit_css({})).group(0)
    pages = weasyprint.HTML(string=f"<style>{css}</style>{html}").render().pages
    return sum(1 for fields in _measure_pages(pages).values() if fields["hasRunts"])


def test_a_render_that_leaves_runts_leaves_none_once_kept():
    pytest.importorskip("weasyprint")
    unkept = kept = 0
    for words in range(12, 60):
        paragraph = [_para(" ".join(["wd"] * words))]
        unkept += _runts(f'<p class="paragraph">{_render_inline(paragraph[0]["content"])}</p>')
        kept += _runts(_render_content(paragraph))
    assert unkept, "the control never produced a runt, so this test cannot fail"
    assert kept == 0


def test_a_one_word_last_line_that_fills_the_measure_is_not_a_runt():
    """A runt is a SHORT last line. One long token that fills the line (a URL's
    tail) is one "word" but no runt. It is longer than a line, so it always ends
    up alone on the last one: under a words-only definition this is a runt at
    every length. (A short one-word line still is: see the control above.)"""
    pytest.importorskip("weasyprint")
    for words in range(12, 40, 3):
        assert _runts(f"<p>{' '.join(['wd'] * words)} {'x' * 70}</p>") == 0


def test_a_line_is_measured_against_its_own_block():
    """A table cell's last line held one word that filled the cell, and read as a
    runt because it was measured against the page (the first real book, p. 555).
    Here the word fills the 50pt block and a small share of the 384pt page."""
    weasyprint = pytest.importorskip("weasyprint")
    from stages.paginate_stage import _measure_pages

    css = "@page{size:400pt 200pt;margin:8pt} p{font-size:9pt;margin:0}"
    html = f'<div style="width:50pt"><p>{"wd " * 6}{"x" * 12}</p></div>'
    pages = weasyprint.HTML(string=f"<style>{css}</style>{html}").render().pages
    assert not any(fields["hasRunts"] for fields in _measure_pages(pages).values())


def test_a_note_piece_never_ends_on_one_word_after_a_forced_break():
    """The cut fell right after the first word following a hard line break: that
    word sat alone on the piece's last line, out of any KEEP's reach (p. 276)."""
    from stages.rendering import _split_note

    content = [{"type": "text", "text": "x" * 20}, {"type": "hardBreak"},
               {"type": "text", "text": "one two three four five"}]
    first, *_ = _split_note(content, 25)
    assert first[-1]["text"] == "one two"
