"""
`structure-propose` (W4, docs/WIRING_PLAN.md): classification/1 -> proposals,
and every proposal, once accepted, is an op `resolve` actually applies.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import validate

import stages  # noqa: F401 -- registers every stage
from publisher_exec import plan
from publisher_stages import RegistryConfig, StageCtx, build_registry
from publisher_structure.overrides import OverrideOp, UNIMPLEMENTED_OPS, _TRANSFORMS, apply_overrides
from publisher_structure.classify_contract import SECTION_LABELS, labels
from publisher_structure.overrides import section_types
from stages.propose_stage import PROPOSAL_OPS, PROPOSAL_PARAMS, block_decision, proposals_for, section_decision, structure_propose

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/agent-proposal/agent-proposal.schema.json").read_text(encoding="utf-8"))


def _section(kind: str, docx_id: str, *lines: str) -> dict:
    return {"type": kind, "sourceRef": {"docxId": docx_id},
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]} for t in lines]}


def _chapter(n: int, title: str, docx_id: str) -> dict:
    return {"type": "chapter", "attrs": {"number": n, "id": f"ch{n}", "title": title},
            "sourceRef": {"docxId": docx_id},
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"Prose of {title}."}]}]}


AST = {"schema": "ast/1", "body": [
    _chapter(1, "ONE", "c1"),
    _chapter(2, "NO!", "c2"),        # prose -> merge into ONE
    _chapter(3, "Aside", "c3"),      # heading -> demote into the one before
    _chapter(4, "FOUR", "c4"),       # agrees -> nothing
    {"type": "part", "attrs": {"title": "II", "id": "p2"}, "content": [
        _chapter(5, "Odd", "c5")]},  # prose, but first in its part -> only a flag
], "frontMatter": [
    _section("dedication", "f0", "For mum", "x"),       # its own label -> agrees, nothing
    _section("preface", "f3", "Foreword", "x"),         # another front type -> reclassify (B5)
    _section("dedication", "f4", "Also by her", "x"),   # alsoBy, valid at the front (B4) -> reclassify
    _section("preface", "f5", "Colophon", "x"),         # a back-only type -> only a flag
    _section("dedication", "f1", "PROLOGUE", "It began."),  # read as a chapter -> start_body
    _section("preface", "f2", "THE STORM", "It rained."),   # also a chapter -> moves with f1
], "backMatter": [
    _section("epilogue", "e1", "EPILOGUE", "After."),     # read as a chapter -> moves with e2
    _section("afterword", "e2", "THE END", "Done."),      # the last one read as a chapter -> end_body
    _section("colophon", "b1", "Set in Garamond", "x"),  # its own label -> agrees, nothing
    _section("notes", "b2", "Note one", "x"),             # read as prose -> only a flag
    _section("appendix", "b3", "Notes", "x"),             # another back type -> reclassify
]}


def _classification(*nodes) -> dict:
    return {"schema": "classification/1",
            "nodes": [{"sourceRef": r, "classification": c, "confidence": s} for r, c, s in nodes],
            "modelInfo": {"modelId": "m/x", "promptVersion": "1.1",
                          "schemaVersion": "classification/1", "cacheHit": False, "costUsd": 0}}


VERDICTS = _classification(("c2", "paragraph", 0.8), ("c3", "heading-2", 0.7),
                           ("c4", "chapter-title", 0.9), ("c5", "paragraph", 0.6),
                           ("f0", "front-dedication", 0.9), ("f1", "chapter-title", 0.7),
                           ("f2", "chapter-title", 0.8), ("b1", "back-colophon", 0.9),
                           ("b2", "paragraph", 0.6),
                           ("e1", "chapter-title", 0.6), ("e2", "chapter-title", 0.7),
                           ("f3", "front-foreword", 0.6), ("f4", "back-also-by", 0.6),
                           ("f5", "back-colophon", 0.6), ("b3", "back-notes", 0.6))


def _accept(p: dict) -> OverrideOp:
    """The op the API's accept route builds: PROPOSAL_PARAMS' parameters copied (B2)."""
    names = {"from": "from_value", "to": "to_value", "value": "value"}
    return OverrideOp(id=f"ov-{p['id']}", sourceRef=p["sourceRef"]["docxId"], op=PROPOSAL_OPS[p["type"]],
                      actor="user:test", rationale=p["rationale"],
                      **{names[k]: p[k] for k in PROPOSAL_PARAMS[p["type"]] if k in p})


def test_each_label_becomes_the_proposal_its_mapping_says():
    got = {p["sourceRef"]["docxId"]: p["type"] for p in proposals_for(VERDICTS, AST)}
    assert got == {"c2": "merge_chapters", "c3": "adjust_heading_level", "c5": "flag_ambiguity",
                   "f1": "start_body", "b2": "flag_ambiguity",
                   "e2": "end_body", "f3": "reclassify", "f4": "reclassify",
                   "f5": "flag_ambiguity", "b3": "reclassify"}


def test_a_retype_names_both_types():
    by_ref = {p["sourceRef"]["docxId"]: p for p in proposals_for(VERDICTS, AST)}
    assert [(by_ref[r]["from"], by_ref[r]["to"]) for r in ("f3", "f4", "b3")] == \
        [("preface", "foreword"), ("dedication", "alsoBy"), ("appendix", "notes")]


@pytest.mark.parametrize("root", ["frontMatter", "backMatter"])
@pytest.mark.parametrize("label", labels())
def test_the_section_decision_table_is_total(label, root):
    """B5: every label at either end lands in exactly one row of the table."""
    kind = "preface" if root == "frontMatter" else "appendix"
    at_boundary, inside = (section_decision(label, kind, root, b) for b in (True, False))
    target = {lb: t for t, lb in SECTION_LABELS.items()}.get(label)
    if label == SECTION_LABELS[kind]:
        expected = (None, None)                                        # agrees
    elif label == "chapter-title":
        expected = (("start_body" if root == "frontMatter" else "end_body", {}), None)
    elif target in section_types(root):
        expected = (("reclassify", {"from": kind, "to": target}),) * 2
    else:
        expected = (("flag_ambiguity", {}),) * 2                       # other end's type, or no section
    assert (at_boundary, inside) == expected


def test_section_proposals_quote_the_opening_and_say_what_moves():
    by_ref = {p["sourceRef"]["docxId"]: p for p in proposals_for(VERDICTS, AST)}
    assert "“PROLOGUE”" in by_ref["f1"]["rationale"]
    assert "the 1 front-matter section(s) after it become chapters" in by_ref["f1"]["rationale"]
    assert "“Note one” as paragraph, not back-notes" in by_ref["b2"]["rationale"]
    assert "the body ends here, so it and the 1 back-matter section(s) before it" in by_ref["e2"]["rationale"]


def test_ids_are_stable_across_rebuilds_and_distinct_per_change():
    first, again = proposals_for(VERDICTS, AST), proposals_for(VERDICTS, AST)
    assert [p["id"] for p in first] == [p["id"] for p in again]
    assert len({p["id"] for p in first}) == len(first)


def test_every_proposal_type_maps_to_an_implemented_op():
    assert set(PROPOSAL_OPS) <= set(SCHEMA["$defs"]["proposal"]["properties"]["type"]["enum"])
    assert all(op in _TRANSFORMS and op not in UNIMPLEMENTED_OPS for op in PROPOSAL_OPS.values())


def test_every_accepted_proposal_applies_to_the_ast_it_was_made_from():
    """What the API's accept route builds, applied the way `resolve` applies it."""
    ops = [_accept(p) for p in proposals_for(VERDICTS, AST)]
    inapplicable, orphaned = [], []
    out = apply_overrides(AST, ops, inapplicable=inapplicable, orphaned=orphaned)
    assert inapplicable == [] and orphaned == []
    assert [c["attrs"]["title"] for c in out["body"] if c["type"] == "chapter"] == \
        ["PROLOGUE", "THE STORM", "ONE", "FOUR", "EPILOGUE", "THE END"]
    assert [s["sourceRef"]["docxId"] for s in out["frontMatter"]] == ["f0", "f3", "f4", "f5"]
    assert [s["sourceRef"]["docxId"] for s in out["backMatter"]] == ["b1", "b2", "b3"]
    assert [s["type"] for s in out["frontMatter"]] == ["dedication", "foreword", "alsoBy", "preface"]
    assert out["backMatter"][-1]["type"] == "notes"


def test_proposing_against_the_corrected_book_converges():
    """B0: structure-propose reads doc-effective/1. Once every proposal is
    accepted, the same verdicts against the corrected book propose nothing new:
    a section start_body or end_body moved is a chapter now, so its verdict
    agrees, and what is left (a flag) is the proposal already accepted."""
    first = proposals_for(VERDICTS, AST)
    ops = [_accept(p) for p in first]
    corrected = apply_overrides(AST, ops)
    again = proposals_for(VERDICTS, corrected)
    assert {p["id"] for p in again} <= {p["id"] for p in first}
    assert not {p["type"] for p in again} & {"start_body", "end_body", "merge_chapters"}


def _run_stage(tmp_path, api_key=None):
    (tmp_path / "c.json").write_text(json.dumps(VERDICTS), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(AST), encoding="utf-8")
    ctx = StageCtx(build_id="b", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_path), cas_root=str(tmp_path / "cas"))
    result = structure_propose(ctx, classification=str(tmp_path / "c.json"),
                               doc_path=str(tmp_path / "a.json"), api_key=api_key)
    blob = next((tmp_path / "cas").rglob(result.artifacts[0].hash))
    return result, json.loads(blob.read_text(encoding="utf-8"))


def _wrangler_keeps(monkeypatch, keep: list[str]):
    """structure-propose with a Wrangler whose scripted answer is `keep`."""
    sys.path.insert(0, str(ROOT / "services/agents/tests"))
    from scripted_provider import ScriptedProvider, answer
    from publisher_agents.runtime import AgentRuntime
    from publisher_agents.structure_wrangler import StructureWrangler
    import stages.propose_stage as propose_stage
    provider = ScriptedProvider(answer({"keep": keep}))
    monkeypatch.setattr(propose_stage, "_wrangler", lambda api_key: StructureWrangler(AgentRuntime(provider)))


def test_the_stage_writes_a_valid_agent_proposal(tmp_path):
    result, document = _run_stage(tmp_path)
    validate(document, SCHEMA)
    assert result.metrics["proposals"] == 10 and document["modelId"] == "m/x"
    assert result.warnings == []


def test_with_a_key_the_wrangler_may_drop_and_reorder(tmp_path, monkeypatch):
    ids = [p["id"] for p in proposals_for(VERDICTS, AST)]
    _wrangler_keeps(monkeypatch, [ids[2], ids[0]])
    result, document = _run_stage(tmp_path, api_key="sk-fake")
    assert [p["id"] for p in document["proposals"]] == [ids[2], ids[0]]
    assert result.metrics["proposals_dropped"] == 8 and result.warnings == []
    validate(document, SCHEMA)


def test_a_failed_review_keeps_every_proposal_and_says_so(tmp_path, monkeypatch):
    _wrangler_keeps(monkeypatch, ["pr-invented"])
    result, document = _run_stage(tmp_path, api_key="sk-fake")
    assert len(document["proposals"]) == 10
    assert [w.code for w in result.warnings] == ["proposal-review-failed"]


def test_without_a_key_no_agent_is_built(tmp_path, monkeypatch):
    import stages.propose_stage as propose_stage

    def no_agent(api_key):
        raise AssertionError("a Wrangler was built with no key")
    monkeypatch.setattr(propose_stage, "_wrangler", no_agent)
    result, document = _run_stage(tmp_path)
    assert len(document["proposals"]) == 10 and result.warnings == []


def test_reachable_exactly_when_structure_infer_is():
    registry = build_registry(RegistryConfig(ingest_impl="ingest"))
    ingest = {"ingest": {"docx_path": "/fake.docx"}}
    assert "structure-propose" not in plan(registry, ingest).order
    with_key = plan(registry, {**ingest, "structure-infer": {"api_key": "sk-fake"}}).order
    assert "structure-propose" in with_key


# ── B8: the block decision table ─────────────────────────────

def _target(kind: str, level: int) -> dict:
    node = {"type": kind, "sourceRef": {"docxId": "t"}, "confidence": 0.5,
            "content": [{"type": "text", "text": "Target"}]}
    return {**node, "attrs": {"level": level}} if kind == "heading" else node


def _words(ast: dict) -> str:
    """Every word in order, chapter titles included: what the integrity gate saw."""
    def walk(node) -> str:
        if isinstance(node, list):
            return " ".join(walk(n) for n in node)
        if not isinstance(node, dict):
            return ""
        title = (node.get("attrs") or {}).get("title", "") if node.get("type") == "chapter" else ""
        own = node.get("text", "") if node.get("type") == "text" else ""
        return " ".join([title, own, walk(node.get("content"))])
    return " ".join(walk(ast["body"]).split())


BLOCKS = [("paragraph", 0)] + [("heading", n) for n in range(1, 7)]


@pytest.mark.parametrize("position", ["first", "middle", "last"])
@pytest.mark.parametrize("kind, level", BLOCKS)
@pytest.mark.parametrize("label", labels())
def test_every_block_row_applies_and_keeps_every_word(label, kind, level, position):
    """B8: the table is total over every label, block and position, and each
    proposal it makes, accepted as the API builds it, applies to the real op
    layer and leaves every word where the integrity gate saw it."""
    target = _target(kind, level)
    other = [_chapter(1, "ONE", "c1")["content"][0], {"type": "paragraph", "content": [{"type": "text", "text": "b"}]}]
    blocks = {"first": [target, *other], "middle": [other[0], target, other[1]], "last": [*other, target]}[position]
    ast = {"schema": "ast/1", "body": [{**_chapter(1, "ONE", "c1"), "content": blocks}]}
    decided = block_decision(label, target, position)
    if decided is None:
        return
    proposal_type, params = decided
    op = _accept({"id": "pr-x", "type": proposal_type, "sourceRef": {"docxId": "t"},
                  "rationale": "r", **params})
    inapplicable, orphaned = [], []
    out = apply_overrides(ast, [op], inapplicable=inapplicable, orphaned=orphaned)
    assert inapplicable == [] and orphaned == [], (proposal_type, params, inapplicable)
    assert _words(out) == _words(ast)


@pytest.mark.parametrize("label, kind, level, position, expected", [
    ("chapter-title", "paragraph", 0, "middle", ("split_chapter", {})),
    ("chapter-title", "paragraph", 0, "last", ("flag_ambiguity", {})),     # split cannot apply
    ("heading-1", "heading", 2, "middle", ("promote_heading", {})),
    ("heading-3", "heading", 1, "middle", ("adjust_heading_level", {})),
    ("heading-3", "heading", 5, "middle", None),                           # sent as heading-3
    ("heading-2", "paragraph", 0, "middle", ("reclassify", {"from": "paragraph", "to": "heading", "value": 2})),
    ("first-paragraph", "heading", 2, "middle", ("reclassify", {"from": "heading", "to": "paragraph"})),
    ("first-paragraph", "paragraph", 0, "middle", None),                   # a prose role agrees
    ("epigraph", "paragraph", 0, "middle", ("reclassify", {"from": "paragraph", "to": "epigraph"})),
    ("verse", "paragraph", 0, "middle", ("flag_ambiguity", {})),
    ("scene-break", "paragraph", 0, "middle", ("flag_ambiguity", {})),
    ("back-notes", "paragraph", 0, "middle", ("flag_ambiguity", {})),
])
def test_the_block_table_rows(label, kind, level, position, expected):
    assert block_decision(label, _target(kind, level), position) == expected


def test_a_chapter_block_verdict_becomes_a_proposal_with_its_parameters():
    chapter = {**_chapter(1, "ONE", "c1"), "content": [
        _chapter(1, "ONE", "c1")["content"][0], _target("paragraph", 0),
        {"type": "paragraph", "content": [{"type": "text", "text": "after"}]}]}
    got = proposals_for(_classification(("t", "heading-2", 0.7)), {"schema": "ast/1", "body": [chapter]})
    assert [(p["type"], p.get("to"), p.get("value")) for p in got] == [("reclassify", "heading", 2)]
    assert "“Target”" in got[0]["rationale"]
