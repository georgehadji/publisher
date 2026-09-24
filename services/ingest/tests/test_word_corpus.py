"""
Ingest over manuscripts Word itself wrote (corpus/word/, made by
make_word_corpus.py through Word's COM API).

The first run of these found five containers the run walk did not know --
a tracked insertion (`w:ins`), a content control (`w:sdt`), a nested table, a
text box, endnotes -- and in every case ingest succeeded and the text was simply
gone. The no-loss check could not see it: it was fed by the same walk. It also
found a Word TOC and a title page merged into chapter one's heading. Every file
here was authored by Word, so the XML is what a real manuscript carries, not what
python-docx's author expected.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from publisher_ingest import docx_rich
from publisher_ingest.docx_to_ast import IngestError, docx_to_ast

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "corpus" / "word"


def _ingest(name: str) -> dict:
    return docx_to_ast(CORPUS / name, store_media=lambda b, t, n: "0" * 64)


def _strings(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("text", "title") and isinstance(value, str):
                yield value
            else:
                yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _text(node) -> str:
    """Every string in a subtree, titles included, one per line."""
    return "\n".join(_strings(node))


def _nodes(node, kind: str) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        if node.get("type") == kind:
            found.append(node)
        for value in node.values():
            found.extend(_nodes(value, kind))
    elif isinstance(node, list):
        for item in node:
            found.extend(_nodes(item, kind))
    return found


@pytest.fixture(scope="module")
def novel() -> dict:
    return _ingest("word-novel.docx")


@pytest.fixture(scope="module")
def technical() -> dict:
    return _ingest("word-technical.docx")


@pytest.fixture(scope="module")
def validator():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "schemas/ast/ast.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize("name", ["word-novel.docx", "word-technical.docx"])
def test_a_word_manuscript_is_a_valid_ast_and_validates_fast(name, validator):
    """The AST's unions were `oneOf`, which jsonschema evaluates in full, every
    branch, at every depth: exponential in nesting. The novel took ~5 s and the
    technical book (a table in a table cell) did not finish in minutes. Now they
    are discriminated by `type`, and each takes well under a second."""
    ast = _ingest(name)
    started = time.perf_counter()
    errors = list(validator.iter_errors(ast))
    elapsed = time.perf_counter() - started
    assert not errors, errors[0].message
    assert elapsed < 5, f"{elapsed:.1f}s: validation is no longer linear in the AST's size"


def test_an_invalid_node_deep_inside_a_nested_table_is_still_rejected(technical, validator):
    ast = copy.deepcopy(technical)
    (outer,) = [t for t in ast["body"][0]["content"] if t["type"] == "table"]
    (inner,) = _nodes(outer["content"], "table")
    cell = inner["content"][0]["content"][0]
    cell["content"][0]["content"][0]["bogus"] = True   # a text node in the inner table

    errors = list(validator.iter_errors(ast))
    assert errors, "an unknown property on a deeply nested node was accepted"

    cell["content"][0]["content"][0].pop("bogus")
    cell["content"][0]["type"] = "not-a-block-type"
    assert list(validator.iter_errors(ast)), "an unknown node type was accepted"


def test_each_union_lists_exactly_its_branches_types():
    """The discriminated unions state their allowed `type`s twice -- once as the
    enum that rejects unknown types, once per branch in the `if`s. Drift between
    the two either rejects a real node or lets an unknown one through unchecked
    (no `if` matches, so nothing is validated)."""
    schema = json.loads((REPO / "schemas/ast/ast.schema.json").read_text(encoding="utf-8"))
    defs = schema["$defs"]

    def types_of(branch: dict) -> set[str]:
        node = defs[branch["$ref"].split("/")[-1]] if "$ref" in branch else branch
        if "allOf" in node and "if" in node["allOf"][0]:
            return {t for b in node["allOf"] for t in types_of(b["then"])}
        return set(node["properties"]["type"]["enum"])

    unions = {name: d for name, d in defs.items() if "allOf" in d and "if" in d["allOf"][0]}
    assert set(unions) == {"bodyNode", "blockNode", "inlineNode", "frontMatterNode", "backMatterNode"}
    for name, union in unions.items():
        seen: set[str] = set()
        for branch in union["allOf"]:
            condition = branch["if"]["properties"]["type"]
            tested = {condition["const"]} if "const" in condition else set(condition["enum"])
            assert tested == types_of(branch["then"]), (name, branch["then"])
            assert not tested & seen, f"{name}: branches overlap on {tested & seen}"
            seen |= tested
        assert set(union["properties"]["type"]["enum"]) == seen, name


def test_a_tracked_insertion_is_text_and_a_tracked_deletion_is_not(novel):
    assert "It had been written in a hurry." in _text(novel["body"])
    assert "It was very short." not in _text(novel)


def test_content_control_text_is_kept(novel):
    assert "wrapped in a content control" in _text(novel["body"])


def test_footnotes_and_endnotes_both_become_notes(novel, technical):
    notes = _text(_nodes(novel, "footnote") + _nodes(technical, "footnote"))
    assert "The postmark was illegible" in notes
    assert "Η σημείωση αυτή βρίσκεται" in notes      # an endnote
    assert "linear wave theory of Airy" in notes      # an endnote


def test_a_non_breaking_hyphen_is_kept_and_a_soft_hyphen_is_not_text(novel):
    body = _text(novel["body"])
    assert "well‑known" in body
    assert "halfrecognised" in body


def test_comments_are_not_book_text(novel):
    assert "Is this the right word?" not in _text(novel)


def test_a_word_toc_and_title_page_do_not_join_chapter_one(novel):
    """Word's TOC entries (style `toc 1`) repeat the chapter titles, upper-case;
    the title page is upper-case too. Both were read as headings adjacent to
    CHAPTER ONE and collapsed into its title."""
    titles = [c["attrs"]["title"] for c in novel["body"]]
    assert titles == ["CHAPTER ONE", "CHAPTER TWO", "ΚΕΦΑΛΑΙΟ ΤΡΙΤΟ"]
    (toc,) = [s for s in novel["frontMatter"] if s["type"] == "toc"]
    # One entry per line, as Word set them -- not one run-on heading.
    entries = [_text(p) for p in toc["content"] if p["type"] == "paragraph"]
    assert entries[0] == "CONTENTS"
    # Word lists every Heading 1, so the colophon too.
    assert [e.split(" ")[-2] for e in entries[1:]] == ["ONE", "TWO", "ΤΡΙΤΟ", "COLOPHON"]
    assert [s["type"] for s in novel["backMatter"]] == ["colophon"]


def test_a_title_page_before_a_page_break_is_not_chapter_one(technical):
    assert [c["attrs"]["title"] for c in technical["body"]] == ["CHAPTER ONE", "CHAPTER TWO"]
    assert "FIELD NOTES ON COASTAL EROSION" in _text(technical["frontMatter"])


def test_a_nested_table_stays_inside_its_cell(technical):
    (outer,) = [t for t in technical["body"][0]["content"] if t["type"] == "table"]
    inner = _nodes(outer["content"], "table")
    assert inner, "the table in the last cell was dropped"
    assert {"winter", "2.6", "summer", "1.2"} <= set(_text(inner).split("\n"))


def test_a_text_box_becomes_a_sidebar_once(technical):
    """Word writes every text box twice: DrawingML, and a VML fallback copy."""
    sidebars = _nodes(technical, "sidebar")
    assert len(sidebars) == 1
    assert "a text box the author floated" in _text(sidebars)
    assert _text(technical).count("a text box the author floated") == 1


# ── word-thesis.docx: the first real manuscript's conventions ─────────────
# A Greek academic book, ingested whole and read as ONE chapter titled with a
# citation: its headings are bold Normal-style lines numbered by hand, and its
# only `Heading` styles were on bibliography entries. The book itself is not in
# this public repo; this file reproduces each convention with synthetic prose.


@pytest.fixture(scope="module")
def thesis() -> dict:
    return _ingest("word-thesis.docx")


def _plain(node: dict) -> str:
    return "".join(t.get("text", "") for t in node.get("content", []) if isinstance(t, dict))


def test_bold_numbered_lines_are_the_chapters(thesis):
    """Chapters are the depth-1 numbers plus the named, unnumbered sections
    (the prologue carries Word list numbering, not a typed one)."""
    assert [c["attrs"]["title"] for c in thesis["body"]] == [
        "Πρόλογος", "2. Εισαγωγή", "3. Η επιστολή", "Επιλογικά εξαγόμενα"]


def test_a_deeper_number_is_a_heading_inside_its_chapter(thesis):
    intro = thesis["body"][1]
    headings = [n for n in intro["content"] if n["type"] == "heading"]
    assert [(h["attrs"]["level"], _plain(h)) for h in headings] == [(2, "2.1 Η βροχή και η θάλασσα")]


def test_numbered_or_bold_alone_is_not_a_heading(thesis):
    """A numbered point in prose (not bold) and a bold diagram label (not
    numbered, not a known section name) stay paragraphs."""
    texts = [_plain(n) for n in thesis["body"][1]["content"] if n["type"] == "paragraph"]
    assert any(t.startswith("1. Ένα αριθμημένο σημείο") for t in texts)
    assert "Πρόκληση" in texts


def test_a_contents_page_in_ordinary_case_is_found(thesis):
    """"Περιεχόμενα" carries a tonos; the pattern is "ΠΕΡΙΕΧΟΜΕΝΑ". A plain
    case-insensitive match does not fold ό to Ο, so it was never found."""
    (toc,) = [s for s in thesis["frontMatter"] if s["type"] == "toc"]
    assert [_plain(p) for p in toc["content"]][:3] == ["Περιεχόμενα", "1. Πρόλογος", "2. Εισαγωγή"]


def test_heading_styled_citations_stay_in_the_bibliography_one_per_entry(thesis):
    """The author styled two adjacent entries `Heading 1` and `Heading 3`. They
    opened chapters titled with citations; then, kept in back matter but joined
    as one title, they became one run-on citation."""
    (bibliography,) = thesis["backMatter"]
    assert bibliography["type"] == "bibliography"
    entries = [_plain(p) for p in bibliography["content"]]
    assert entries[0] == "Βιβλιογραφία"
    assert [e.split(",")[0] for e in entries[1:]] == ["Αλεξίου", "Βασιλείου", "Γεωργίου", "Δημητρίου"]


def test_a_picture_in_a_footnote_is_kept(thesis):
    """Notes had no media sink, so ingest refused the whole book."""
    intro = thesis["body"][1]["content"]
    types = [n["type"] for n in intro]
    assert "footnote" in types and "figure" in types
    assert types.index("figure") == types.index("footnote") + 1


def test_smartart_text_is_kept(thesis):
    """A SmartArt's words live in a separate data part; neither the walk nor
    the no-loss check read it, so both diagrams' labels vanished unseen."""
    (diagram,) = _nodes(thesis, "sidebar")
    assert [_plain(p) for p in diagram["content"]] == ["Ερώτηση", "Έλεγχος", "Ορισμός"]


def test_the_no_loss_check_counts_smartart_text():
    """So a walk that stops emitting diagrams fails ingest instead of passing."""
    import docx

    sources = docx_rich.source_texts(docx.Document(str(CORPUS / "word-thesis.docx")))
    assert {"Ερώτηση", "Έλεγχος", "Ορισμός"} <= set(sources)


def test_an_equation_is_refused_not_dropped():
    with pytest.raises(IngestError, match="equation"):
        _ingest("word-equation.docx")


def test_the_no_loss_check_does_not_trust_the_walk(monkeypatch):
    """The oracle reads the XML itself. Blind the walk to tracked insertions --
    exactly the state it was in before -- and ingest must now fail, where it
    used to succeed with the text gone."""
    monkeypatch.setattr(docx_rich, "TRANSPARENT", docx_rich.TRANSPARENT - {f"{docx_rich.W}ins"})
    with pytest.raises(IngestError, match="did not reach the AST"):
        _ingest("word-novel.docx")
