"""
A footnote's call number sits at the end of the paragraph that cites it.

Ingest places each note right after its paragraph, and the renderer emitted the
note's `<span class="footnote">` after that paragraph's `</p>`. weasyprint then
generated the call (`::footnote-call`) in an anonymous line of its own: a lone
"1" under every cited paragraph -- all 477 of them in the first real book, and
visible on every page that had a note. Measured on a real render here.
"""

from __future__ import annotations

import pytest

from stages.rendering import ast_to_html, emit_css

weasyprint = pytest.importorskip("weasyprint")


def _lines(ast: dict) -> list[str]:
    """Every rendered line's text, page by page, in order."""
    from weasyprint.formatting_structure import boxes

    html = f"<style>{emit_css({'chapterOpenings': {'dropCap': False}})}</style>" + ast_to_html(ast)
    document = weasyprint.HTML(string=html).render()
    lines: list[str] = []

    def text_of(box) -> str:
        if isinstance(box, boxes.TextBox):
            return box.text
        return "".join(text_of(c) for c in getattr(box, "children", []) or [])

    def walk(box):
        if isinstance(box, boxes.LineBox):
            lines.append(text_of(box).strip())
            return
        for child in getattr(box, "children", []) or []:
            walk(child)

    for page in document.pages:
        walk(page._page_box)
    return [line for line in lines if line]


def _book(content: list) -> dict:
    return {"schema": "ast/1", "metadata": {"title": "T"}, "frontMatter": [], "backMatter": [],
            "body": [{"type": "chapter", "attrs": {"number": 1, "title": "One", "id": "ch1"},
                      "content": content}]}


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _note(text: str) -> dict:
    return {"type": "footnote", "attrs": {"number": 1}, "content": [{"type": "text", "text": text}]}


def test_the_call_is_on_the_cited_paragraphs_last_line():
    lines = _lines(_book([_para("The claim is made here."), _note("A source for it."),
                          _para("The argument goes on.")]))
    assert not any(line.isdigit() for line in lines), lines
    assert any(line.startswith("The claim is made here.") and line.endswith("1") for line in lines), lines


def test_two_notes_on_one_paragraph_both_attach_to_it():
    lines = _lines(_book([_para("Two sources back this."), _note("First."), _note("Second."),
                          _para("Then more prose.")]))
    assert not any(line.isdigit() for line in lines), lines
    assert any(line.startswith("Two sources back this.") and line.endswith("12") for line in lines), lines


def test_a_note_with_no_paragraph_before_it_still_renders():
    html = ast_to_html(_book([_note("A note that opens the chapter."), _para("Prose.")]))
    assert '<span class="footnote"> A note that opens the chapter.</span>' in html


def test_a_note_inside_its_paragraph_passes_the_integrity_gate(tmp_path):
    """Moving the note inside `<p>` first broke the gate on the real book: the
    HTML read "claim.Source" where the source AST reads "claim. Source"."""
    import json
    from datetime import datetime, timezone

    from publisher_stages import StageCtx
    from stages.structure_stage import ast_assemble

    ast = _book([_para("The claim is made here."), _note("A source for it."), _para("More.")])
    (tmp_path / "extract.html").write_text(ast_to_html(ast), encoding="utf-8")
    (tmp_path / "source.json").write_text(json.dumps(ast), encoding="utf-8")
    ctx = StageCtx(build_id="fn", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_path), allow_stub_engines=True)
    result = ast_assemble(ctx, html=str(tmp_path / "extract.html"), source=str(tmp_path / "source.json"))
    assert result.metrics["integrity_ok"] == 1.0
