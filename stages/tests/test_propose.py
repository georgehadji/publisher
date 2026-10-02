"""
`structure-propose` (W4, docs/WIRING_PLAN.md): classification/1 -> proposals,
and every proposal, once accepted, is an op `resolve` actually applies.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import validate

import stages  # noqa: F401 -- registers every stage
from publisher_exec import plan
from publisher_stages import RegistryConfig, StageCtx, build_registry
from publisher_structure.overrides import OverrideOp, UNIMPLEMENTED_OPS, _TRANSFORMS, apply_overrides
from stages.propose_stage import PROPOSAL_OPS, proposals_for, structure_propose

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/agent-proposal/agent-proposal.schema.json").read_text(encoding="utf-8"))


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
]}


def _classification(*nodes) -> dict:
    return {"schema": "classification/1",
            "nodes": [{"sourceRef": r, "classification": c, "confidence": s} for r, c, s in nodes],
            "modelInfo": {"modelId": "m/x", "promptVersion": "1.1",
                          "schemaVersion": "classification/1", "cacheHit": False, "costUsd": 0}}


VERDICTS = _classification(("c2", "paragraph", 0.8), ("c3", "heading-2", 0.7),
                           ("c4", "chapter-title", 0.9), ("c5", "paragraph", 0.6))


def test_each_label_becomes_the_proposal_its_mapping_says():
    got = {p["sourceRef"]["docxId"]: p["type"] for p in proposals_for(VERDICTS, AST)}
    assert got == {"c2": "merge_chapters", "c3": "adjust_heading_level", "c5": "flag_ambiguity"}


def test_ids_are_stable_across_rebuilds_and_distinct_per_change():
    first, again = proposals_for(VERDICTS, AST), proposals_for(VERDICTS, AST)
    assert [p["id"] for p in first] == [p["id"] for p in again]
    assert len({p["id"] for p in first}) == len(first)


def test_every_proposal_type_maps_to_an_implemented_op():
    assert set(PROPOSAL_OPS) <= set(SCHEMA["$defs"]["proposal"]["properties"]["type"]["enum"])
    assert all(op in _TRANSFORMS and op not in UNIMPLEMENTED_OPS for op in PROPOSAL_OPS.values())


def test_every_accepted_proposal_applies_to_the_ast_it_was_made_from():
    """What the API's accept route builds, applied the way `resolve` applies it."""
    ops = [OverrideOp(id=f"ov-{p['id']}", sourceRef=p["sourceRef"]["docxId"],
                      op=PROPOSAL_OPS[p["type"]], actor="user:test", rationale=p["rationale"])
           for p in proposals_for(VERDICTS, AST)]
    inapplicable, orphaned = [], []
    out = apply_overrides(AST, ops, inapplicable=inapplicable, orphaned=orphaned)
    assert inapplicable == [] and orphaned == []
    assert [c["attrs"]["title"] for c in out["body"] if c["type"] == "chapter"] == ["ONE", "FOUR"]


def _run_stage(tmp_path, api_key=None):
    (tmp_path / "c.json").write_text(json.dumps(VERDICTS), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(AST), encoding="utf-8")
    ctx = StageCtx(build_id="b", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_path), cas_root=str(tmp_path / "cas"))
    result = structure_propose(ctx, classification=str(tmp_path / "c.json"),
                               ast=str(tmp_path / "a.json"), api_key=api_key)
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
    assert result.metrics["proposals"] == 3 and document["modelId"] == "m/x"
    assert result.warnings == []


def test_with_a_key_the_wrangler_may_drop_and_reorder(tmp_path, monkeypatch):
    ids = [p["id"] for p in proposals_for(VERDICTS, AST)]
    _wrangler_keeps(monkeypatch, [ids[2], ids[0]])
    result, document = _run_stage(tmp_path, api_key="sk-fake")
    assert [p["id"] for p in document["proposals"]] == [ids[2], ids[0]]
    assert result.metrics["proposals_dropped"] == 1 and result.warnings == []
    validate(document, SCHEMA)


def test_a_failed_review_keeps_every_proposal_and_says_so(tmp_path, monkeypatch):
    _wrangler_keeps(monkeypatch, ["pr-invented"])
    result, document = _run_stage(tmp_path, api_key="sk-fake")
    assert len(document["proposals"]) == 3
    assert [w.code for w in result.warnings] == ["proposal-review-failed"]


def test_without_a_key_no_agent_is_built(tmp_path, monkeypatch):
    import stages.propose_stage as propose_stage

    def no_agent(api_key):
        raise AssertionError("a Wrangler was built with no key")
    monkeypatch.setattr(propose_stage, "_wrangler", no_agent)
    result, document = _run_stage(tmp_path)
    assert len(document["proposals"]) == 3 and result.warnings == []


def test_reachable_exactly_when_structure_infer_is():
    registry = build_registry(RegistryConfig(ingest_impl="ingest"))
    ingest = {"ingest": {"docx_path": "/fake.docx"}}
    assert "structure-propose" not in plan(registry, ingest).order
    with_key = plan(registry, {**ingest, "structure-infer": {"api_key": "sk-fake"}}).order
    assert "structure-propose" in with_key
