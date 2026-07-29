"""Tests for override layer."""

import json

from publisher_structure.overrides import (
    OverrideOp, OverrideSet, OrphanedOp,
    rebase_overrides, apply_overrides,
    _build_source_map, _fuzzy_match,
)


def test_override_op_creation():
    op = OverrideOp(
        id="ov-001",
        sourceRef="docx:body/p[1]",
        op="reclassify",
        from_value="heading:2",
        to_value="chapter-title",
        actor="user:001",
    )
    assert op.id == "ov-001"
    assert op.op == "reclassify"
    assert op.actor == "user:001"


def test_override_set():
    ops = [
        OverrideOp(id="ov-001", sourceRef="src-1", op="reclassify",
                   from_value="p", to_value="chapter-title", actor="user:1"),
    ]
    override_set = OverrideSet(document_id="doc-001", ast_version=1, ops=ops)
    assert override_set.schema == "overrides/1"
    assert len(override_set.ops) == 1


def test_apply_reclassify():
    ast = {
        "type": "chapter",
        "content": [
            {
                "type": "paragraph",
                "sourceRef": {"docxId": "p1"},
                "content": [{"type": "text", "text": "Hello"}],
            }
        ],
    }
    op = OverrideOp(id="ov-001", sourceRef="p1", op="reclassify",
                    from_value="paragraph", to_value="chapter-title",
                    actor="user:1")
    effective = apply_overrides(ast, [op])
    # Find the reclassified node
    for node in effective["content"]:
        if node.get("_override") == "ov-001":
            assert node["type"] == "chapter-title"
            break
    else:
        assert False, "Override not applied"


def test_apply_retitle():
    ast = {
        "type": "chapter",
        "attrs": {"title": "Old Title", "number": 1, "id": "ch1"},
        "sourceRef": {"docxId": "ch1"},
    }
    op = OverrideOp(id="ov-002", sourceRef="ch1", op="retitle",
                    value="New Title", actor="user:1")
    effective = apply_overrides(ast, [op])
    for node in effective["content"] if "content" in effective else [effective]:
        pass
    # Chapter title should be updated
    assert effective.get("attrs", {}).get("title") == "New Title"


def test_rebase_exact_match():
    """Rebase with exact sourceRef match should succeed."""
    old_ast = {"body": [{"type": "chapter", "attrs": {"id": "ch1"}, "sourceRef": {"docxId": "p1"}}]}
    new_ast = {"body": [{"type": "chapter", "attrs": {"id": "ch1"}, "sourceRef": {"docxId": "p1"}}]}
    
    ops = [OverrideOp(id="ov-001", sourceRef="p1", op="reclassify",
                      from_value="p", to_value="chapter-title", actor="user:1")]
    
    rebased, orphaned = rebase_overrides(ops, old_ast, new_ast)
    assert len(rebased) == 1
    assert len(orphaned) == 0


def test_rebase_orphaned():
    """Override referencing a node that doesn't exist in new AST."""
    old_ast = {"body": [{"type": "p", "sourceRef": {"docxId": "p-old"}}]}
    new_ast = {"body": []}
    
    ops = [OverrideOp(id="ov-001", sourceRef="p-old", op="reclassify",
                      from_value="p", to_value="chapter-title", actor="user:1")]
    
    rebased, orphaned = rebase_overrides(ops, old_ast, new_ast)
    assert len(rebased) == 0
    assert len(orphaned) == 1
    assert orphaned[0].reason == "no_source_ref"


def test_build_source_map():
    ast = {
        "body": [
            {"type": "chapter", "attrs": {"id": "ch1"}, "sourceRef": {"docxId": "p1"},
             "content": [{"type": "text", "text": "Hello world"}]}
        ]
    }
    sm = _build_source_map(ast)
    assert "p1" in sm
    assert sm["p1"]["text"] == "Hello world"


def test_fuzzy_match():
    source_map = {
        "ref-1": {"sourceRef": "ref-1", "text": "The quick brown fox"},
        "ref-2": {"sourceRef": "ref-2", "text": "Something completely different"},
    }
    matches = _fuzzy_match("quick brown fox", source_map, cutoff=0.5)
    assert len(matches) >= 1
    assert matches[0]["sourceRef"] == "ref-1"
