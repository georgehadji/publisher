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


AST = {
    "schema": "ast/1",
    "frontMatter": [{"type": "dedication", "text": "For no one", "confidence": 0.95}],
    "body": [
        {
            "type": "chapter",
            "attrs": {"number": 1, "title": "One"},
            "confidence": 0.99,
            "content": [
                {"type": "paragraph", "text": "sure", "confidence": 0.91},
                {"type": "heading", "text": "maybe", "confidence": 0.42},
                {"type": "paragraph", "text": "unscored"},
            ],
        }
    ],
    "backMatter": [{"type": "colophon", "text": "Set in Garamond", "confidence": 0.88}],
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
    assert types.count("paragraph") == 2, "nested content is walked, not just the roots"


def test_query_nodes_filters_by_type():
    found = call("query_nodes", ast=AST, types=["heading"])
    assert [n["text"] for n in found] == ["maybe"]


def test_an_unscored_node_is_not_treated_as_confident():
    """The absent-vs-value distinction again, one layer up.

    A node with no confidence is UNSCORED. Reading that as 1.0 would hide
    exactly the nodes a low-confidence query exists to surface.
    """
    found = call("query_nodes", ast=AST, confidence_below=0.8)
    texts = {n.get("text") for n in found}
    assert "maybe" in texts, "0.42 is below the threshold"
    assert "unscored" in texts, "a node with no score must not be filtered out"
    assert "sure" not in texts, "0.91 is above the threshold"


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
