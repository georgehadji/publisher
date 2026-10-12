"""
`layout-review` (W11): the Compositor's caller. Which pages it is shown, the
verdict document it writes, and that it is reachable only with a key and
feeds nothing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import validate

import stages  # noqa: F401 -- registers every stage
import stages.layout_review_stage as layout_review_stage
from publisher_exec import plan
from publisher_stages import RegistryConfig, StageCtx, build_registry
from stages.layout_review_stage import MAX_FINDINGS, findings_for, layout_review

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/layout-review/layout-review.schema.json").read_text(encoding="utf-8"))
SCAN = {"pages": 20, "hyphenStacks": [7, 7], "rivers": [11]}


def _report(*warned: str) -> dict:
    return {"schema": "preflight/1", "status": "warn", "summary": {"passed": 1, "failed": 0},
            "checks": [{"code": c, "status": "warn", "severity": "warning", "humanMessage": "x"} for c in warned]
            + [{"code": "bleed", "status": "pass", "severity": "error", "humanMessage": "x"}]}


def _pagemap(widows=(), runts=()) -> dict:
    return {"pages": [{"pageNumber": n, "hasOrphans": False, "hasWidows": n in widows, "hasRunts": n in runts}
                      for n in range(1, 21)]}


@pytest.fixture(autouse=True)
def _scan(monkeypatch):
    """measure_spacing needs poppler and a real PDF; the scan is spacing.py's own concern."""
    import publisher_prepress.spacing as spacing
    monkeypatch.setattr(spacing, "measure_spacing", lambda pdf: {"scan": SCAN})


def test_only_warned_checks_are_looked_at_widows_and_runts_first():
    found = findings_for(_report("composition", "rivers"), _pagemap(widows=(5,), runts=(2, 5)), "p.pdf")
    assert [f["id"] for f in found] == ["widow-p5", "runt-p2", "runt-p5", "river-p11"]
    assert findings_for(_report("hyphen-stacks"), _pagemap(widows=(5,)), "p.pdf") == [
        {"id": "hyphen-stack-p7", "kind": "hyphen-stack", "page": 7}]
    assert findings_for(_report(), _pagemap(widows=(5,)), "p.pdf") == []


def _ctx(tmp_path) -> StageCtx:
    return StageCtx(build_id="b", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                    memory_budget_mb=256, work_dir=str(tmp_path), cas_root=str(tmp_path / "cas"))


def _run(tmp_path, report: dict, pagemap: dict):
    for name, doc in (("r.json", report), ("m.json", pagemap)):
        (tmp_path / name).write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "p.pdf").write_bytes(b"%PDF-1.3")
    result = layout_review(_ctx(tmp_path), report=str(tmp_path / "r.json"), pdf_path=str(tmp_path / "p.pdf"),
                           pagemap_path=str(tmp_path / "m.json"), api_key="sk-fake")
    blob = next((tmp_path / "cas").rglob(result.artifacts[0].hash))
    document = json.loads(blob.read_text(encoding="utf-8"))
    validate(document, SCHEMA)
    return result, document


def _compositor_answers(monkeypatch, *turns):
    sys.path.insert(0, str(ROOT / "services/agents/tests"))
    from scripted_provider import ScriptedProvider
    from publisher_agents.compositor import Compositor
    from publisher_agents.runtime import AgentRuntime
    provider = ScriptedProvider(*turns)
    monkeypatch.setattr(layout_review_stage, "_compositor", lambda api_key: Compositor(AgentRuntime(provider)))
    return provider


def test_each_finding_gets_the_compositors_verdict(tmp_path, monkeypatch):
    from scripted_provider import answer
    _compositor_answers(monkeypatch, answer({"verdicts": [
        {"finding": "widow-p5", "verdict": "confirmed"}, {"finding": "river-p11", "verdict": "not-a-defect"}]}))
    result, document = _run(tmp_path, _report("composition", "rivers"), _pagemap(widows=(5,), runts=(2,)))
    assert document["findings"] == [
        {"kind": "widow", "page": 5, "verdict": "confirmed"},
        {"kind": "runt", "page": 2, "verdict": "unreviewed"},       # left out of the answer
        {"kind": "river", "page": 11, "verdict": "not-a-defect"}]
    assert document["modelId"] and result.warnings == []
    assert result.metrics == {"findings": 3, "reviewed": 2, "confirmed": 1}


def test_over_the_cap_the_rest_are_unreviewed_and_it_says_so(tmp_path, monkeypatch):
    from scripted_provider import answer
    provider = _compositor_answers(monkeypatch, answer({"verdicts": []}))
    result, document = _run(tmp_path, _report("composition"), _pagemap(runts=range(1, MAX_FINDINGS + 4)))
    shown = json.loads(provider.seen[0]["messages"][1]["content"])["findings"]
    assert len(shown) == MAX_FINDINGS and len(document["findings"]) == MAX_FINDINGS + 3
    assert [w.code for w in result.warnings] == ["layout-review-capped"]


def test_a_failed_review_leaves_every_finding_unreviewed_and_says_so(tmp_path, monkeypatch):
    from scripted_provider import answer
    _compositor_answers(monkeypatch, answer({"verdicts": [{"finding": "runt-p9", "verdict": "confirmed"}]}))
    result, document = _run(tmp_path, _report("composition"), _pagemap(widows=(5,)))
    assert document["findings"] == [{"kind": "widow", "page": 5, "verdict": "unreviewed"}]
    assert [w.code for w in result.warnings] == ["layout-review-failed"]


def test_nothing_warned_no_compositor_is_built(tmp_path, monkeypatch):
    def no_agent(api_key):
        raise AssertionError("a Compositor was built with nothing to look at")
    monkeypatch.setattr(layout_review_stage, "_compositor", no_agent)
    result, document = _run(tmp_path, _report(), _pagemap(widows=(5,)))
    assert document == {"schema": "layout-review/1", "agentId": "compositor", "findings": []}


def test_reachable_only_with_a_key_and_feeds_nothing():
    registry = build_registry(RegistryConfig(ingest_impl="ingest"))
    design = registry.selected_implementation("design-compile")
    finish = registry.selected_implementation("finish")
    build = {"ingest": {"docx_path": "/fake.docx"},
             design: {"designspec_path": "d.json", "profile_name": "Generic 6x9"},
             finish: {"profile_name": "Generic 6x9"}, "preflight": {"profile_name": "Generic 6x9"}}
    without = plan(registry, build).order
    assert "preflight" in without and "layout-review" not in without
    assert "layout-review" in plan(registry, {**build, "layout-review": {"api_key": "sk-fake"}}).order
    consumers = {d.name for d in registry.all() if "layout-review/1" in d.inputs.values()}
    assert consumers == set()
