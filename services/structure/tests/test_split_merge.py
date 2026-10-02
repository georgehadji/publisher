"""
`split` and `merge`: the fix for a chapter boundary ingest got wrong.

A run-on chapter (ingest missed a heading) is split at the block that should
have opened the next one; a false chapter (ingest took a shouted line for a
heading) is merged back into the one before. Neither may lose a word, and each
must undo the other.
"""

from __future__ import annotations

import pytest

from publisher_structure.overrides import InapplicableOverride, OverrideOp, apply_overrides


def _p(text: str, ref: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}],
            "sourceRef": {"docxId": ref}}


def _chapter(number: int, title: str, ref: str, *blocks: dict) -> dict:
    return {"type": "chapter", "attrs": {"number": number, "id": f"ch{number}", "title": title},
            "sourceRef": {"docxId": ref}, "content": list(blocks)}


AST = {"schema": "ast/1", "body": [
    _chapter(1, "One", "c1", _p("a", "p1"), _p("TWO", "p2"), _p("b", "p3")),
    _chapter(2, "Three", "c2", _p("c", "p4")),
]}


def _op(op: str, ref: str, id_: str = "") -> OverrideOp:
    return OverrideOp(id=id_ or f"ov-{op}-{ref}", sourceRef=ref, op=op, actor="user:t")


def _outline(ast: dict) -> list:
    return [(c["attrs"]["number"], c["attrs"]["title"],
             [b["content"][0]["text"] for b in c["content"]]) for c in ast["body"]]


def _words(ast: dict) -> list[str]:
    """Titles and text, in book order -- what the integrity gate compares."""
    words: list[str] = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            words.extend(((node.get("attrs") or {}).get("title") or "").split())
            words.extend((node.get("text") or "").split())
            walk(node.get("content"))
            walk(node.get("body"))

    walk(ast)
    return words


def test_split_opens_a_chapter_titled_by_the_block_it_splits_at():
    out = apply_overrides(AST, [_op("split", "p2")])
    assert _outline(out) == [(1, "One", ["a"]), (2, "TWO", ["b"]), (3, "Three", ["c"])]
    assert out["body"][1]["sourceRef"] == {"docxId": "p2"}   # targetable, e.g. by merge
    assert _words(out) == _words(AST)                        # every word, in order


def test_merge_joins_a_chapter_to_the_one_before_keeping_its_title_as_text():
    out = apply_overrides(AST, [_op("merge", "c2")])
    assert _outline(out) == [(1, "One", ["a", "TWO", "b", "Three", "c"])]
    assert _words(out) == _words(AST)


def test_split_then_merge_is_the_original_book():
    split = apply_overrides(AST, [_op("split", "p2")])
    back = apply_overrides(split, [_op("merge", "p2")])
    assert _outline(back) == _outline(AST)


def test_split_inside_a_part_renumbers_the_chapters_after_it():
    ast = {"body": [
        {"type": "part", "attrs": {"title": "I", "id": "pt1"}, "content": [AST["body"][0]]},
        AST["body"][1],
    ]}
    out = apply_overrides(ast, [_op("split", "p2")])
    part = out["body"][0]["content"]
    assert [c["attrs"]["number"] for c in part] == [1, 2]
    assert out["body"][1]["attrs"]["number"] == 3


def test_split_does_not_touch_the_input_and_ids_are_deterministic():
    before = _outline(AST)
    a = apply_overrides(AST, [_op("split", "p2")])
    b = apply_overrides(AST, [_op("split", "p2")])
    assert _outline(AST) == before
    assert a["body"][1]["attrs"]["id"] == b["body"][1]["attrs"]["id"]


@pytest.mark.parametrize("op, ref, reason", [
    ("split", "p1", "already opens the chapter"),
    ("split", "p3", "the new chapter would be empty"),
    ("merge", "c1", "no chapter precedes it"),
    ("merge", "p1", "not a chapter"),
])
def test_an_op_that_does_not_fit_is_reported_not_fatal_and_not_silent(op, ref, reason):
    """The log is append-only: a fatal op would leave the book unbuildable."""
    skipped: list = []
    out = apply_overrides(AST, [_op(op, ref)], skipped)
    assert out is AST
    assert [(o.op, r) for o, r in skipped] and reason in skipped[0][1]
    with pytest.raises(InapplicableOverride):
        apply_overrides(AST, [_op(op, ref)])   # no list to report in: it raises


def test_an_op_that_matches_nothing_is_an_orphan_left_to_the_api():
    assert apply_overrides(AST, [_op("split", "nowhere")]) is AST
