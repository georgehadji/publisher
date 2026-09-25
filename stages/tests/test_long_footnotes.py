"""
A footnote longer than a page is set in pieces the renderer can carry over.

weasyprint 62 cannot break a note across pages. In the first real book, notes of
40-70 lines (long Greek quotations) filled whole footnote areas and left the body
a single line: four orphans that preflight rightly refused, and that weasyprint
cannot avoid by itself (it will not leave a page with no body text). Now a long
note is split at sentence ends into pieces, the footnote area is capped so the
body always keeps a share of the page, and the numbers are the renderer's own,
so a split note still reads as one call and one numbered note.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess

import pytest

from stages.rendering import (NOTE_PIECE_CHARS, NOTE_SPLIT_CHARS, _split_note, ast_to_html,
                              emit_css)
from stages.text_stream import ast_text

WORDS = "ὁ Σωκράτης λέγει ὅτι ἡ ἀρετὴ ἐπιστήμη ἐστίν καὶ οὐδεὶς ἑκὼν ἁμαρτάνει.".split()


def _prose(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(words))


def _book(paragraphs: int, seed: int = 3) -> dict:
    rng = random.Random(seed)
    content = []
    for i in range(paragraphs):
        content.append({"type": "paragraph", "content": [{"type": "text", "text": _prose(rng, rng.randint(60, 160))}]})
        if i % 3 == 0:
            note = [{"type": "text", "text": _prose(rng, rng.choice([30, 400, 700])),
                     "marks": [{"type": "emphasis"}]}]
            content.append({"type": "footnote", "attrs": {"number": 1}, "content": note})
    return {"schema": "ast/1", "metadata": {"title": "T"}, "frontMatter": [], "backMatter": [],
            "body": [{"type": "chapter", "attrs": {"number": 1, "title": "Ένα", "id": "ch1"},
                      "content": content}]}


def test_a_long_note_is_split_at_sentence_ends_keeping_its_marks():
    text = "Πρώτη πρόταση εδώ. " * 120
    pieces = _split_note([{"type": "text", "text": text, "marks": [{"type": "emphasis"}]}],
                         NOTE_PIECE_CHARS)
    assert len(pieces) > 1
    assert all(sum(len(n["text"]) for n in p) <= NOTE_PIECE_CHARS for p in pieces)
    assert all(p[0]["text"].endswith(".") for p in pieces[:-1])
    assert all(p[0]["marks"] == [{"type": "emphasis"}] for p in pieces)
    assert " ".join(p[0]["text"] for p in pieces).strip() == text.strip()


def test_calls_and_notes_are_numbered_once_per_note():
    book = _book(30)
    html = ast_to_html(book)
    notes = sum(1 for n in book["body"][0]["content"] if n["type"] == "footnote")
    calls = re.findall(r'<span class="note-call" data-n="(\d+)">', html)
    firsts = re.findall(r'<span class="footnote" data-n="(\d+)">', html)
    assert calls == firsts == [str(i) for i in range(1, notes + 1)]
    assert html.count('class="footnote footnote-cont"') > 0   # the long ones were split


def test_a_split_note_passes_the_integrity_gate(tmp_path):
    import json
    from datetime import datetime, timezone

    from publisher_stages import StageCtx
    from stages.structure_stage import ast_assemble

    book = _book(30)
    (tmp_path / "extract.html").write_text(ast_to_html(book), encoding="utf-8")
    (tmp_path / "source.json").write_text(json.dumps(book), encoding="utf-8")
    ctx = StageCtx(build_id="ln", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=256, work_dir=str(tmp_path), allow_stub_engines=True)
    result = ast_assemble(ctx, html=str(tmp_path / "extract.html"), source=str(tmp_path / "source.json"))
    assert result.metrics["integrity_ok"] == 1.0


def test_long_notes_leave_no_orphan_and_nothing_past_the_type_area():
    weasyprint = pytest.importorskip("weasyprint")
    from weasyprint.formatting_structure import boxes

    from stages.paginate_stage import _measure_pages

    css = emit_css({"trimSize": {"width": 170, "height": 240}, "chapterOpenings": {"dropCap": False}})
    doc = weasyprint.HTML(string=f"<style>{css}</style>" + ast_to_html(_book(90))).render()
    orphans = [p for p, m in _measure_pages(doc.pages).items() if m.get("hasOrphans")]
    assert orphans == []
    past = 0
    calls, markers = [], []
    for page in doc.pages:
        box = page._page_box
        foot = box.content_box_y() + box.height + 0.5
        for b in box.descendants():
            if isinstance(b, boxes.LineBox) and "footnote" in ((getattr(b, "element", None) is not None
                                                                and b.element.get("class")) or ""):
                past += b.position_y + b.height > foot
            tag = getattr(b, "element_tag", "") or ""
            if type(b).__name__ == "InlineBox" and tag.endswith("::after") and b.element.get("class") == "note-call":
                calls.append("".join(t.text for t in b.descendants() if isinstance(t, boxes.TextBox)))
            if type(b).__name__ == "InlineBox" and tag.endswith("::footnote-marker"):
                text = "".join(t.text for t in b.descendants() if isinstance(t, boxes.TextBox)).rstrip(". ")
                if text:
                    markers.append(text)
    assert past == 0
    expected = [str(i) for i in range(1, len(calls) + 1)]
    assert calls == expected and markers == expected


def test_typst_folds_the_pieces_back_into_one_footnote(tmp_path):
    pandoc = shutil.which("pandoc")
    if not pandoc:
        pytest.skip("pandoc not installed")
    from stages.typst_stages import FOOTNOTE_FILTER_LUA

    note = [{"type": "text", "text": "Πρώτη πρόταση εδώ. " * 120}]
    assert len(note[0]["text"]) > NOTE_SPLIT_CHARS
    html = ast_to_html({"schema": "ast/1", "metadata": {"title": "T"}, "frontMatter": [], "backMatter": [],
                        "body": [{"type": "chapter", "attrs": {"number": 1, "title": "Ένα", "id": "c"},
                                  "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Κείμενο."}]},
                                              {"type": "footnote", "attrs": {"number": 1}, "content": note}]}]})
    assert html.count("footnote-cont") >= 2
    lua = tmp_path / "notes.lua"
    lua.write_text(FOOTNOTE_FILTER_LUA, encoding="utf-8")
    typ = subprocess.run([pandoc, "--from=html", "--to=typst", "--lua-filter", str(lua)],
                         input=html.encode("utf-8"), capture_output=True, check=True).stdout.decode("utf-8")
    assert typ.count("#footnote[") == 1
    assert " ".join(typ.split()).count("Πρώτη πρόταση εδώ.") == 120


def test_a_piece_never_starts_where_the_text_has_no_space():
    """The first real book: "... Στο (" then a link, a text run of its own with no
    space in it. Cutting between the two runs let the next piece's leading space
    into the book -- "Στο ( http" -- and the integrity gate refused the build."""
    url = "http://users.uoa.gr/~nektar/history/tributes/" + "x" * 60
    content = [{"type": "text", "text": "Πρώτη πρόταση εδώ. " * 44 + "Στο ("},
               {"type": "text", "text": url, "marks": [{"type": "link", "attrs": {"href": url}}]},
               {"type": "text", "text": "). " + "Άλλη πρόταση. " * 40}]
    note = {"type": "footnote", "attrs": {"number": 1}, "content": content}
    book = {"schema": "ast/1", "metadata": {"title": "T"}, "frontMatter": [], "backMatter": [],
            "body": [{"type": "chapter", "attrs": {"number": 1, "title": "Ένα", "id": "c"},
                      "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Κείμενο."}]}, note]}]}
    from publisher_structure.rules import extract_text_from_html, normalize_text

    html = ast_to_html(book)
    assert "footnote-cont" in html
    assert normalize_text(extract_text_from_html(html)) == normalize_text(ast_text(book))
    assert "( http" not in normalize_text(extract_text_from_html(html))


def test_a_full_piece_does_not_cut_the_next_run_after_its_first_character():
    """The first real book again: a URL with no break in it overfilled a piece,
    the room left went negative, and "not found" passed the break test -- the
    next run was cut after "«", and a space went into the book."""
    from stages.rendering import _note_cut

    assert _note_cut("«…τὴν πολλὴν στρατιὰν. Καὶ", -57) == 0
    assert _note_cut("«…τὴν πολλὴν στρατιὰν. Καὶ", 0) == 0
