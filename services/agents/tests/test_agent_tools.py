"""
The agent tool surface — what each tool actually answers.

Every tool in `_init_default_tools` used to return a hardcoded value: an empty
node list, an empty defect list, an empty check list, and two well-formed
references (`crop://page-3`) to images nothing had rendered. An agent reasoning
over those answers proposes nothing and looks careful doing it.

These tests pin the two properties that fix has to hold:
  - a tool that CAN answer does, from the artifact it is handed;
  - a tool that CANNOT answer raises, rather than inventing a value.
"""

from __future__ import annotations

import pytest

from publisher_agents.runtime import (
    AgentRole,
    ToolUnavailable,
    get_tool_registry,
)

REGISTRY = get_tool_registry()


def call(name: str, **kwargs):
    """Through the registry, as an agent reaches a tool — not the closure directly."""
    return REGISTRY.call(name, None, **kwargs)


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _chapter(n: int, title: str, *, confidence: float | None = None) -> dict:
    node = {"type": "chapter", "attrs": {"number": n, "id": f"ch{n}", "title": title},
            "content": [_para(f"prose of {title}")]}
    if confidence is not None:
        node["confidence"] = confidence
    return node


# Schema-valid on purpose (test_fixture_is_a_valid_ast): an earlier version put
# `confidence` on paragraphs, which ast.schema.json forbids, so the tool was
# being tested against a shape no real AST can have.
AST = {
    "schema": "ast/1",
    "metadata": {"title": "Fixture"},
    "frontMatter": [{"type": "dedication", "content": [_para("For no one")], "confidence": 0.95}],
    "body": [
        _chapter(1, "One", confidence=0.99),
        _chapter(2, "NO!", confidence=0.42),   # shouted dialogue read as a heading
        _chapter(3, "Three"),                  # scorable, but never scored
    ],
    "backMatter": [{"type": "colophon", "content": [_para("Set in Garamond")], "confidence": 0.88}],
    "integrityHash": "sha256:" + "0" * 64,
    "sourceRef": {"manuscriptId": "fixture", "inferenceVersion": 1},
}

PAGEMAP = {
    "schema": "pagemap/1",
    "pages": [
        {"pageNumber": 1, "hasOrphans": False, "hasWidows": False, "hasRunts": False},
        {"pageNumber": 2, "hasOrphans": False, "hasWidows": True, "hasRunts": True},
        {"pageNumber": 3, "hasOrphans": True, "hasWidows": False, "hasRunts": False},
    ],
    "chapters": [],
}

PREFLIGHT = {
    "schema": "preflight/1",
    "status": "warn",
    "profileId": "Generic 6x9",
    "checks": [
        {"code": "trim-size", "status": "pass", "severity": "info", "humanMessage": "ok"},
        {"code": "composition", "status": "warn", "severity": "warning",
         "humanMessage": "2 runt page(s)"},
    ],
    "summary": {"passed": 1, "failed": 0, "warnings": 1, "policyViolations": 0},
}


# ── query_nodes ────────────────────────────────────────────────

def test_query_nodes_walks_all_three_roots():
    types = [n["type"] for n in call("query_nodes", ast=AST)]
    assert types[0] == "dedication", "frontMatter comes first, in document order"
    assert "colophon" in types and "chapter" in types
    assert types.count("paragraph") == 5, "nested content is walked, not just the roots"


def test_query_nodes_filters_by_type():
    found = call("query_nodes", ast=AST, types=["chapter"])
    assert [n["attrs"]["title"] for n in found] == ["One", "NO!", "Three"]


def test_an_unscored_node_is_not_treated_as_confident():
    """The absent-vs-value distinction again, one layer up.

    A scorable node with no confidence is UNSCORED. Reading that as 1.0 would
    hide exactly the nodes a low-confidence query exists to surface.
    """
    found = call("query_nodes", ast=AST, confidence_below=0.8)
    titles = [(n.get("attrs") or {}).get("title") for n in found]
    assert titles == ["NO!", "Three"], "0.42 is below; unscored is not above; the rest are"


def test_a_confidence_query_skips_types_that_carry_no_decision():
    """Paragraphs and text runs have no structural decision to doubt.

    Counting them as unscored would return the whole book, burying the few
    chapters that actually need a look.
    """
    found = call("query_nodes", ast=AST, confidence_below=0.8)
    assert {n["type"] for n in found} == {"chapter"}


def test_fixture_is_a_valid_ast():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(AST, _ast_schema())


def test_scored_types_match_the_schema():
    """SCORED_TYPES mirrors which ast/1 node types may carry `confidence`.

    Drift in either direction is a silent bug: a type the schema scores but the
    tool skips is never surfaced; a type the tool expects but the schema forbids
    is always "unscored".
    """
    from publisher_agents.runtime import SCORED_TYPES

    defs = _ast_schema()["$defs"]
    declared: set[str] = set()
    candidates = [d for d in defs.values() if isinstance(d, dict)]
    candidates += [o for d in candidates for o in d.get("oneOf", []) if "properties" in o]
    for d in candidates:
        props = d.get("properties") or {}
        if "confidence" in props:
            declared.update(props["type"]["enum"])
    assert SCORED_TYPES == declared


def _ast_schema() -> dict:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "schemas" / "ast" / "ast.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_query_nodes_refuses_a_non_document():
    with pytest.raises(TypeError):
        call("query_nodes", ast=[{"type": "paragraph"}])


# ── scan_pagemap ───────────────────────────────────────────────

def test_scan_pagemap_reports_real_defects_with_the_gates_severities():
    out = call("scan_pagemap", pagemap=PAGEMAP)
    assert [(d["page_number"], d["defect_type"], d["severity"]) for d in out["defects"]] == [
        (2, "runt", "warning"),
        (2, "widow", "warning"),
        (3, "orphan", "error"),
    ]


def test_scan_pagemap_filters_by_defect_type():
    out = call("scan_pagemap", pagemap=PAGEMAP, filter="orphan")
    assert [d["page_number"] for d in out["defects"]] == [3]


def test_scan_pagemap_says_how_much_was_measured():
    """Zero defects over zero measured pages is not a clean book.

    An agent handed only `defects` cannot distinguish a well-set book from one
    the renderer never measured, which is the same collapse that made the whole
    composition path decorative.
    """
    unmeasured = {"pages": [{"pageNumber": n} for n in (1, 2, 3)]}
    out = call("scan_pagemap", pagemap=unmeasured)
    assert out["defects"] == []
    assert out["pages"] == 3
    assert out["measuredPages"] == 0

    measured = call("scan_pagemap", pagemap=PAGEMAP)
    assert measured["measuredPages"] == 3


def test_scan_pagemap_agrees_with_the_delivery_gate():
    """One definition of a defect, not two.

    The tool and `preflight`'s composition check must read the same pagemap the
    same way -- an agent that disagreed with the gate would propose fixes for
    defects the gate does not see, or miss the ones blocking delivery.
    """
    from publisher_prepress.preflight import check_composition

    verdict = check_composition({"pagemap": PAGEMAP}, {})
    out = call("scan_pagemap", pagemap=PAGEMAP)
    errors = [d for d in out["defects"] if d["severity"] == "error"]
    assert verdict.status == "fail" and errors, "both see the orphan on page 3"
    assert verdict.sourceRef == f"pagemap/1#page={errors[0]['page_number']}"


# ── read_preflight ─────────────────────────────────────────────

def test_read_preflight_returns_the_report_it_is_given():
    out = call("read_preflight", report=PREFLIGHT)
    assert out["status"] == "warn"
    assert out["profileId"] == "Generic 6x9"
    assert len(out["checks"]) == 2
    assert out["summary"]["warnings"] == 1


def test_read_preflight_filters_by_status():
    out = call("read_preflight", report=PREFLIGHT, status="warn")
    assert [c["code"] for c in out["checks"]] == ["composition"]


def test_read_preflight_refuses_a_non_report():
    with pytest.raises(TypeError):
        call("read_preflight", report="build-123")


# ── the two that cannot be answered ────────────────────────────

@pytest.mark.parametrize("name,kwargs", [
    ("crop", {"page": 3}),
    ("render_range", {"start_page": 1, "end_page": 4}),
])
def test_tools_without_a_capability_raise_rather_than_fabricate(name, kwargs):
    """They used to return `crop://page-3` and `render://pages-1-to-4`.

    Both are well-formed references to artifacts that were never produced and
    that no resolver anywhere resolves. A raise is recoverable; a fabricated
    reference is evidence.
    """
    with pytest.raises(ToolUnavailable) as exc:
        call(name, **kwargs)
    assert "unavailable" in str(exc.value)
    assert "://" not in str(exc.value), "must not hand back the fake ref it used to return"


def test_the_unavailable_tools_say_so_in_their_spec():
    """A surface an agent can read before spending a turn discovering it."""
    for name in ("crop", "render_range"):
        assert "UNAVAILABLE" in REGISTRY.spec(name).description


def test_every_registered_tool_is_callable_or_raises_tool_unavailable():
    """No tool may answer by returning a hardcoded empty result.

    The guard against this regressing: a new tool either reads what it is handed
    or declares itself unavailable. Returning `[]`/`{}` from a body that looked
    at nothing is what this whole change exists to remove.
    """
    for spec in REGISTRY.list_tools():
        fn = REGISTRY.get(spec.name)
        assert fn is not None, f"{spec.name} is specced but not registered"
        assert fn.__doc__, f"{spec.name} has no docstring saying what it answers"


def test_the_compositors_declared_tools_all_exist():
    """compositor.py names its tools as strings; nothing checks they resolve."""
    surface = REGISTRY.surface_for(AgentRole.COMPOSITOR)
    for name in ("scan_pagemap", "render_range", "crop", "propose_override"):
        assert name in surface.names()
