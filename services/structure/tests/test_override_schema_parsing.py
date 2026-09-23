"""
`resolve` reads the override log in the shape the schema -- and the API -- write.

`OverrideOp` predates schemas/overrides/overrides.schema.json and names things
differently (`from_value` for `from`, `created_at` for `at`, a flat string
`sourceRef` for the schema's object). `resolve` did `OverrideOp(**op)`, so the
first schema-valid op raised `TypeError: unexpected keyword argument 'from'`.
It had only ever been fed the dataclass's own shape, by tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from publisher_structure.overrides import (
    _OP_FIELDS,
    _OP_REQUIRES,
    _REQUIRED,
    _SOURCE_REF_FIELDS,
    MalformedOverrideOp,
    apply_overrides,
    parse_override_op,
    parse_overrides,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "schemas/overrides/overrides.schema.json")
    .read_text(encoding="utf-8")
)
OP_SCHEMA = SCHEMA["$defs"]["overrideOp"]


def _op(**fields) -> dict:
    return {
        "id": "ov-1",
        "sourceRef": {"docxId": "p1"},
        "op": "retitle",
        "value": "Two",
        "actor": "user:reviewer",
        "at": "2026-01-01T00:00:00Z",
        **fields,
    }


def _document(*ops: dict) -> dict:
    return {"schema": "overrides/1", "documentId": "ms-1", "astVersion": 1, "ops": list(ops)}


# ── The fix: a schema-valid op parses and applies ───────────────

def test_a_schema_valid_log_parses_and_applies():
    """End to end, from the exact shape PATCH stores to a changed AST."""
    jsonschema = pytest.importorskip("jsonschema")
    document = _document(
        _op(),
        _op(id="ov-2", op="reclassify", sourceRef={"docxId": "p2"}, value=None,
            **{"from": "paragraph", "to": "epigraph"}),
    )
    del document["ops"][1]["value"]
    jsonschema.validate(document, SCHEMA)   # the fixture is what the API accepts

    ast = {"type": "doc", "content": [
        {"type": "chapter", "sourceRef": {"docxId": "p1"}, "attrs": {"title": "One"},
         "content": [{"type": "paragraph", "sourceRef": {"docxId": "p2"},
                      "content": [{"type": "text", "text": "x"}]}]},
    ]}
    effective = apply_overrides(ast, parse_overrides(document))
    chapter = effective["content"][0]
    assert chapter["attrs"]["title"] == "Two"
    assert chapter["content"][0]["type"] == "epigraph"


def test_every_schema_field_lands_on_the_right_attribute():
    op = parse_override_op(_op(
        sourceRef={"docxId": "p9", "contentHash": "a" * 64, "fallbackText": "the text"},
        path="/body/0", rationale="why", **{"from": "a", "to": "b"},
    ))
    assert (op.id, op.op, op.actor, op.path) == ("ov-1", "retitle", "user:reviewer", "/body/0")
    assert (op.sourceRef, op.sourceContentHash, op.sourceFallbackText) == ("p9", "a" * 64, "the text")
    assert (op.from_value, op.to_value, op.value) == ("a", "b", "Two")
    assert (op.rationale, op.created_at) == ("why", "2026-01-01T00:00:00Z")


# ── Strict: nothing is dropped, nothing is guessed ──────────────

@pytest.mark.parametrize("raw,complaint", [
    (_op(colour="red"), "unknown: colour"),
    (_op(sourceRef={"docxId": "p1", "page": 3}), "unknown: sourceRef.page"),
    ({k: v for k, v in _op().items() if k != "actor"}, "missing: actor"),
    (_op(sourceRef={"contentHash": "a" * 64}), "missing: sourceRef.docxId"),
    (_op(sourceRef="p1"), "missing: sourceRef.docxId"),   # the old flat shape
    ({k: v for k, v in _op().items() if k != "value"}, "missing: value"),
    (_op(op="reclassify", **{"from": "paragraph"}), "missing: to"),
    (_op(op="flag_ambiguity"), "missing: rationale"),
])
def test_a_malformed_op_is_refused_by_name(raw, complaint):
    with pytest.raises(MalformedOverrideOp, match=complaint):
        parse_override_op(raw)


@pytest.mark.parametrize("document", [
    {"ops": [_op()]},                                  # no schema id
    {"schema": "overrides/2", "ops": [_op()]},
    {"schema": "overrides/1"},                         # no ops at all
    [_op()],
])
def test_anything_but_an_overrides_document_is_refused(document):
    """Read as zero ops, any of these builds the book as if no reviewer had
    touched it."""
    with pytest.raises(MalformedOverrideOp):
        parse_overrides(document)


def test_an_empty_log_is_zero_ops():
    assert parse_overrides(_document()) == []


# ── The mapping tables cannot drift from the schema ─────────────

def test_op_fields_are_the_schemas():
    assert set(_OP_FIELDS) | {"sourceRef"} == set(OP_SCHEMA["properties"])


def test_source_ref_fields_are_the_schemas():
    assert set(_SOURCE_REF_FIELDS) == set(OP_SCHEMA["properties"]["sourceRef"]["properties"])


def test_required_fields_are_the_schemas():
    assert set(_REQUIRED) == set(OP_SCHEMA["required"])


def test_per_op_requirements_are_the_schemas():
    declared = {
        rule["if"]["properties"]["op"]["const"]: tuple(rule["then"]["required"])
        for rule in OP_SCHEMA["allOf"]
    }
    assert _OP_REQUIRES == declared
