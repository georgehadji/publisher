"""
An override the layer cannot apply must fail the build, not vanish.

`schemas/overrides/overrides.schema.json` declares twelve ops; four have
transforms. The other eight used to hit a bare `continue` under a comment
naming two of them, so a schema-valid, API-accepted override was discarded with
no error, no diagnostic and no metric -- the effective document silently
disagreed with the decision a reviewer had been told was recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from publisher_structure.overrides import (
    UNIMPLEMENTED_OPS,
    OverrideOp,
    UnsupportedOverrideOp,
    apply_overrides,
)

SCHEMA = Path(__file__).resolve().parents[3] / "schemas/overrides/overrides.schema.json"

# Same node shape the rest of test_overrides.py uses: the AST carries
# `sourceRef` as {"docxId": ...}, the op carries the bare id.
AST = {
    "type": "chapter",
    "content": [
        {
            "type": "heading",
            "sourceRef": {"docxId": "p1"},
            "attrs": {"title": "One"},
            "content": [{"type": "text", "text": "One"}],
        }
    ],
}


def _op(op: str) -> OverrideOp:
    return OverrideOp(id=f"ov-{op}", sourceRef="p1", op=op, actor="tester")


@pytest.mark.parametrize("op", sorted(UNIMPLEMENTED_OPS))
def test_every_declared_but_unimplemented_op_is_refused(op):
    with pytest.raises(UnsupportedOverrideOp) as exc:
        apply_overrides(AST, [_op(op)])
    assert op in str(exc.value)
    assert "no transform implements it yet" in str(exc.value)


def test_an_op_that_is_not_in_the_schema_at_all_says_so():
    with pytest.raises(UnsupportedOverrideOp) as exc:
        apply_overrides(AST, [_op("teleport")])
    assert "not a valid override op at all" in str(exc.value)


def test_implemented_ops_still_apply():
    out = apply_overrides(AST, [
        OverrideOp(id="ov-1", sourceRef="p1", op="retitle", actor="tester", value="Two"),
    ])
    assert out["content"][0]["attrs"]["title"] == "Two"
    assert AST["content"][0]["attrs"]["title"] == "One", "input AST must not be mutated"


def test_no_overrides_is_not_an_error():
    assert apply_overrides(AST, []) is AST


def test_unimplemented_list_matches_the_schema():
    """The guard against the two drifting apart again.

    Implementing an op means deleting its name from UNIMPLEMENTED_OPS; adding one
    to the schema means adding it here. This test fails either way round.
    """
    from publisher_structure.overrides import _TRANSFORMS

    def _find(node):
        if isinstance(node, dict):
            if node.get("type") == "string" and "reclassify" in (node.get("enum") or []):
                return node["enum"]
            for value in node.values():
                found = _find(value)
                if found:
                    return found
        if isinstance(node, list):
            for value in node:
                found = _find(value)
                if found:
                    return found
        return None

    declared = set(_find(json.loads(SCHEMA.read_bytes())))
    assert set(_TRANSFORMS) | UNIMPLEMENTED_OPS == declared
    assert not (set(_TRANSFORMS) & UNIMPLEMENTED_OPS)
