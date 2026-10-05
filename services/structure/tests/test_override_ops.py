"""
Every override op does what it says, or says why it did not (docs/WIRING_PLAN.md W1).

Two failures this guards against. An op that matched a node and could not act on
it -- a retitle aimed at a paragraph, a reclassify whose `from` was stale -- came
back unchanged with no report. And `delete` set a `_deleted` mark no renderer
reads, so a "deleted" paragraph still printed. Every result here is also checked
against ast.schema.json: the effective document is what the renderers read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from publisher_structure.overrides import (
    InapplicableOverride, OverrideOp, _public, apply_overrides,
)

SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "schemas/ast/ast.schema.json")
                    .read_text(encoding="utf-8"))


def _p(text: str, ref: str, **attrs) -> dict:
    node = {"type": "paragraph", "content": [{"type": "text", "text": text}], "sourceRef": {"docxId": ref}}
    return {**node, "attrs": attrs} if attrs else node


def _h(text: str, ref: str, level: int) -> dict:
    return {"type": "heading", "attrs": {"level": level},
            "content": [{"type": "text", "text": text}], "sourceRef": {"docxId": ref}}


def _chapter(number: int, title: str, ref: str, *blocks: dict) -> dict:
    return {"type": "chapter", "attrs": {"number": number, "id": f"ch{number}", "title": title},
            "sourceRef": {"docxId": ref}, "content": list(blocks)}


AST = {
    "schema": "ast/1",
    "integrityHash": "sha256:" + "0" * 64,
    "sourceRef": {"manuscriptId": "test", "inferenceVersion": 1, "createdAt": "2026-01-01T00:00:00Z"},
    "body": [
        _chapter(1, "One", "c1", _p("a", "p1"), _h("Section", "h1", 1), _p("b", "p2"),
                 _h("Sub", "h2", 2), _p("c", "p3")),
        _chapter(2, "Two", "c2", _p("d", "p4"), {"type": "blockquote", "sourceRef": {"docxId": "q1"},
                                                 "content": [_p("quoted", "p5")]}),
        _chapter(3, "Three", "c3", _p("e", "p6")),
    ],
}


def _op(op: str, ref: str, id_: str = "", **fields) -> OverrideOp:
    return OverrideOp(id=id_ or f"ov-{op}-{ref}", sourceRef=ref, op=op, actor="user:t", **fields)


def _apply(*ops: OverrideOp) -> dict:
    out = apply_overrides(AST, list(ops))
    errors = sorted(Draft202012Validator(SCHEMA).iter_errors(_public(out)), key=str)
    assert not errors, f"the effective document is not a valid AST: {errors[0].message}"
    return out


def _refused(op: OverrideOp, reason: str) -> None:
    skipped: list = []
    assert apply_overrides(AST, [op], skipped) is AST, "an op that does not fit changes nothing"
    assert skipped and reason in skipped[0][1], skipped


def _outline(ast: dict) -> list:
    def block(b):
        if b["type"] == "heading":
            return f"h{b['attrs']['level']}:{b['content'][0]['text']}"
        if b["type"] == "paragraph":
            return b["content"][0]["text"]
        return b["type"]
    return [(c["attrs"]["number"], c["attrs"].get("title"), [block(b) for b in c["content"]])
            for c in ast["body"]]


def _node(ast: dict, ref: str) -> dict:
    from publisher_structure.overrides import _find_by_source_ref
    return _find_by_source_ref(ast, ref)


def test_the_fixture_is_a_valid_ast():
    _apply()


# ── retitle / reclassify: matched but unable is reported ────────

def test_retitle_of_a_node_with_no_title_is_reported_not_silent():
    _refused(_op("retitle", "p1", value="X"), "a paragraph has no title")


def test_retitle_longer_than_the_schema_allows_is_refused():
    _refused(_op("retitle", "c1", value="x" * 513), "title breaks the schema's maxLength (512)")


def test_reclassify_with_a_stale_from_is_reported():
    _refused(_op("reclassify", "p1", from_value="heading", to_value="paragraph"),
             "it is a paragraph, not the heading the op expected")


def test_reclassify_to_a_shape_the_schema_rejects_is_refused():
    _refused(_op("reclassify", "p1", from_value="paragraph", to_value="chapter"), "cannot become a chapter")


def test_a_paragraph_reclassified_into_a_blockquote_and_back_is_unchanged():
    wrapped = apply_overrides(AST, [_op("reclassify", "p1", from_value="paragraph", to_value="blockquote")])
    assert _node(wrapped, "p1")["type"] == "blockquote"
    back = apply_overrides(wrapped, [_op("reclassify", "p1", "ov-back", from_value="blockquote",
                                         to_value="paragraph")])
    assert _public(_node(back, "p1")) == _public(_node(AST, "p1"))


# ── set_attr ───────────────────────────────────────────────────

def test_set_attr_sets_an_attribute_the_type_declares():
    out = _apply(_op("set_attr", "c2", path="/attrs/startsOn", value="recto"))
    assert out["body"][1]["attrs"]["startsOn"] == "recto"


def test_set_attr_with_no_value_removes_the_attribute():
    set_ = _op("set_attr", "p1", "ov-1", path="/attrs/alignment", value="center")
    unset = _op("set_attr", "p1", "ov-2", path="/attrs/alignment")
    assert _node(_apply(set_), "p1")["attrs"]["alignment"] == "center"
    assert "alignment" not in (_node(_apply(set_, unset), "p1").get("attrs") or {})


@pytest.mark.parametrize("path, value, reason", [
    ("/attrs/startsOn", "sideways", "is not one of"),
    ("/attrs/title", "X", "`title` is set by retitle"),
    ("/attrs/number", 9, "`number` is derived"),
    ("/attrs/colour", "red", "a chapter has no attribute `colour`"),
    ("/title", "X", "is not /attrs/<name>"),
])
def test_set_attr_refuses_what_it_does_not_own_or_the_schema_rejects(path, value, reason):
    _refused(_op("set_attr", "c1", path=path, value=value), reason)


# ── flag / resolve ─────────────────────────────────────────────

def test_resolve_ambiguity_clears_one_flag_or_all():
    flag1 = _op("flag_ambiguity", "p1", "ov-f1", rationale="a")
    flag2 = _op("flag_ambiguity", "p1", "ov-f2", rationale="b")
    one = apply_overrides(AST, [flag1, flag2, _op("resolve_ambiguity", "p1", "ov-r", value="ov-f1")])
    assert [f["id"] for f in _node(one, "p1")["_flags"]] == ["ov-f2"]
    all_ = apply_overrides(AST, [flag1, flag2, _op("resolve_ambiguity", "p1", "ov-r")])
    assert "_flags" not in _node(all_, "p1")


def test_resolving_a_flag_that_is_not_there_is_reported():
    _refused(_op("resolve_ambiguity", "p1"), "carries no flag")


# ── promote / demote ───────────────────────────────────────────

def test_promote_raises_a_heading_one_level():
    assert _node(_apply(_op("promote", "h2")), "h2")["attrs"]["level"] == 1


def test_promoting_a_level_one_heading_opens_a_chapter_titled_by_it():
    out = _apply(_op("promote", "h1"))
    assert _outline(out) == [
        (1, "One", ["a"]), (2, "Section", ["b", "h2:Sub", "c"]),
        (3, "Two", ["d", "blockquote"]), (4, "Three", ["e"]),
    ]


def test_demoting_a_chapter_makes_it_a_level_one_heading_of_the_one_before():
    out = _apply(_op("demote", "c2"))
    assert _outline(out)[0][2][-3:] == ["h1:Two", "d", "blockquote"]
    assert [n for n, *_ in _outline(out)] == [1, 2]


def test_promote_then_demote_is_the_original_book():
    up = apply_overrides(AST, [_op("promote", "h1")])
    back = apply_overrides(up, [_op("demote", "h1", "ov-down")])
    assert _outline(back) == _outline(AST)


@pytest.mark.parametrize("op, ref, reason", [
    ("promote", "c1", "already the top level"),
    ("promote", "p1", "a paragraph has no level"),
    ("demote", "c1", "no chapter precedes it"),
])
def test_level_ops_that_do_not_fit_are_reported(op, ref, reason):
    _refused(_op(op, ref), reason)


def test_a_level_six_heading_cannot_be_demoted():
    deep = [_op("demote", "h2", f"ov-d{i}") for i in range(4)]       # 2 -> 6
    out = apply_overrides(AST, deep)
    skipped: list = []
    apply_overrides(out, [_op("demote", "h2", "ov-d9")], skipped)
    assert "already the lowest" in skipped[0][1]


# ── delete / insert ────────────────────────────────────────────

def test_delete_removes_the_node_from_the_book():
    out = _apply(_op("delete", "p2"))
    assert _outline(out)[0][2] == ["a", "h1:Section", "h2:Sub", "c"]


def test_deleting_a_chapter_renumbers_the_ones_after_it():
    assert [n for n, *_ in _outline(_apply(_op("delete", "c2")))] == [1, 2]


def test_deleting_a_chapters_only_block_is_refused():
    _refused(_op("delete", "p6"), "the only node in its chapter")


def test_insert_adds_a_scene_break_that_a_later_delete_can_take_out():
    out = _apply(_op("insert", "p1", value="sceneBreak"))
    blocks = out["body"][0]["content"]
    assert blocks[1]["type"] == "sceneBreak"
    again = apply_overrides(out, [_op("delete", blocks[1]["sourceRef"]["docxId"])])
    assert _outline(again) == _outline(AST)


@pytest.mark.parametrize("ref, value, reason", [
    ("p1", "Some new words", "inserted text would bypass the integrity gate"),
    ("p5", "sceneBreak", "cannot go inside a blockquote"),
])
def test_insert_refuses_text_and_places_a_break_cannot_go(ref, value, reason):
    _refused(_op("insert", ref, value=value), reason)


# ── start_body: the front/body boundary ingest got wrong ───────

def _section(kind: str, ref: str, *blocks: dict) -> dict:
    return {"type": kind, "sourceRef": {"docxId": ref}, "confidence": 0.5, "content": list(blocks)}


BOOK = {**AST, "frontMatter": [
    _section("dedication", "f1", _p("DEDICATION", "fp1"), _p("For mum.", "fp2")),
    _section("preface", "f2", _p("THE STORM", "fp3"), _p("It rained.", "fp4")),       # really chapter 1
    _section("toc", "f3", _p("THE CALM", "fp5"), _p("It stopped.", "fp6"), _p("Then.", "fp7")),
], "backMatter": [_section("colophon", "b1", _p("Set in Garamond", "bp1"))]}


def _text_in_order(ast: dict) -> str:
    def walk(node) -> str:
        if isinstance(node, list):
            return " ".join(walk(n) for n in node)
        if not isinstance(node, dict):
            return ""
        own = [node["text"]] if node.get("type") == "text" else []
        title = [(node.get("attrs") or {}).get("title", "")] if node.get("type") == "chapter" else []
        return " ".join(title + own + [walk(node.get(k)) for k in ("frontMatter", "body", "backMatter", "content")])
    return " ".join(walk(ast).split())


def _apply_to_book(*ops: OverrideOp) -> dict:
    out = apply_overrides(BOOK, list(ops))
    errors = sorted(Draft202012Validator(SCHEMA).iter_errors(_public(out)), key=str)
    assert not errors, f"the effective document is not a valid AST: {errors[0].message}"
    return out


def test_start_body_moves_that_section_and_the_rest_of_the_front_matter_into_the_body():
    out = _apply_to_book(_op("start_body", "f2"))
    assert [s["sourceRef"]["docxId"] for s in out["frontMatter"]] == ["f1"]
    assert _outline(out)[:3] == [(1, "THE STORM", ["It rained."]), (2, "THE CALM", ["It stopped.", "Then."]),
                                 (3, "One", ["a", "h1:Section", "b", "h2:Sub", "c"])]
    assert [c["attrs"]["number"] for c in out["body"]] == [1, 2, 3, 4, 5]
    # The section's id goes with it, so an op aimed at it still lands.
    assert [c["sourceRef"]["docxId"] for c in out["body"][:2]] == ["f2", "f3"]
    assert len({c["attrs"]["id"] for c in out["body"]}) == 5
    # Every word stays in the book, in order: the integrity gate already passed.
    assert _text_in_order(out) == _text_in_order(BOOK)


def test_start_body_can_empty_the_front_matter():
    out = _apply_to_book(_op("start_body", "f1"))
    assert out["frontMatter"] == [] and _outline(out)[0][1] == "DEDICATION"


@pytest.mark.parametrize("book, ref, reason", [
    (BOOK, "c2", "not a front-matter section"),
    (BOOK, "b1", "not a front-matter section"),
    (BOOK, "fp3", "not a front-matter section"),
    ({**BOOK, "frontMatter": [_section("titlePage", "t1", _p("ALONE", "tp1"))]}, "t1",
     "nothing left once its first block titles it"),
    ({**BOOK, "frontMatter": [_section("preface", "t1", _p("TITLE", "tp1"), _p("x", "tp2")),
                              _p("a stray line", "tp3")]}, "t1", "a paragraph, not a section"),
    ({**BOOK, "frontMatter": [_section("preface", "t1", {"type": "sceneBreak"}, _p("x", "tp2"))]}, "t1",
     "no text to title"),
])
def test_start_body_refuses_what_it_cannot_turn_into_chapters(book, ref, reason):
    skipped: list = []
    assert apply_overrides(book, [_op("start_body", ref)], skipped) is book
    assert skipped and reason in skipped[0][1], skipped


def test_ops_on_nothing_are_orphans_left_to_the_api():
    for name in ("promote", "demote", "delete", "insert", "set_attr", "resolve_ambiguity", "start_body"):
        assert apply_overrides(AST, [_op(name, "nowhere", value="sceneBreak", path="/attrs/role")]) is AST


def test_an_op_may_aim_at_a_node_an_earlier_op_created_and_is_not_an_orphan():
    inserted = apply_overrides(AST, [_op("insert", "p1", value="sceneBreak")])
    break_id = inserted["body"][0]["content"][1]["sourceRef"]["docxId"]
    orphaned: list = []
    apply_overrides(AST, [_op("insert", "p1", value="sceneBreak"), _op("delete", break_id)], [], orphaned)
    assert orphaned == []
    apply_overrides(AST, [_op("delete", "nowhere")], [], orphaned)
    assert [op.sourceRef for op in orphaned] == ["nowhere"]
