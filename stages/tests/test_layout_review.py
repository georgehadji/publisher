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


def _pagemap(widows=(), runts=(), refs=None) -> dict:
    """`refs`: page -> {"widowRefs"|"runtRefs": [docxId, ...]} (K1)."""
    return {"pages": [{"pageNumber": n, "hasOrphans": False, "hasWidows": n in widows, "hasRunts": n in runts,
                       **(refs or {}).get(n, {})} for n in range(1, 21)]}


def _doc(**fits) -> dict:
    """A doc-effective/1 of paragraphs: docxId -> fit (None: as designed)."""
    return {"schema": "ast/1", "body": [{"type": "chapter", "attrs": {"number": 1}, "content": [
        {"type": "paragraph", "sourceRef": {"docxId": ref}, **({"attrs": {"fit": fit}} if fit else {}),
         "content": [{"type": "text", "text": ref}]} for ref, fit in fits.items()]}]}


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


def _run(tmp_path, report: dict, pagemap: dict, doc: dict | None = None):
    for name, doc in (("r.json", report), ("m.json", pagemap), ("d.json", doc or _doc())):
        (tmp_path / name).write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "p.pdf").write_bytes(b"%PDF-1.3")
    result = layout_review(_ctx(tmp_path), report=str(tmp_path / "r.json"), pdf_path=str(tmp_path / "p.pdf"),
                           pagemap_path=str(tmp_path / "m.json"), doc_path=str(tmp_path / "d.json"),
                           api_key="sk-fake")
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
    assert result.metrics == {"findings": 3, "reviewed": 2, "confirmed": 1, "proposals": 0,  # no refs
                              "fit_cleared": 0, "fit_still_defective": 0, "fit_unmeasured": 0}


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


def test_a_confirmed_widow_or_runt_proposes_the_next_fit_step_by_rule(tmp_path, monkeypatch):
    from scripted_provider import answer
    _compositor_answers(monkeypatch, answer({"verdicts": [
        {"finding": "widow-p5", "verdict": "confirmed"}, {"finding": "runt-p7", "verdict": "confirmed"},
        {"finding": "runt-p9", "verdict": "not-a-defect"}]}))
    refs = {5: {"widowRefs": ["p-a"]}, 7: {"runtRefs": ["p-b", "p-c", "p-gone"]}, 9: {"runtRefs": ["p-d"]}}
    result, document = _run(tmp_path, _report("composition"), _pagemap(widows=(5,), runts=(7, 9), refs=refs),
                            _doc(**{"p-a": None, "p-b": "tight", "p-c": "tighter", "p-d": None}))
    widow, runt, rejected = document["findings"]
    assert [(p["docxId"], p["type"], p["value"]) for p in widow["proposals"]] == [("p-a", "fit_paragraph", "tight")]
    # p-b steps on; p-c is at the last step and p-gone is no paragraph: both left to a human
    assert [(p["docxId"], p["value"]) for p in runt["proposals"]] == [("p-b", "tighter")]
    assert "proposals" not in rejected
    assert result.metrics["proposals"] == 2


def test_a_proposal_is_the_same_proposal_on_every_rebuild():
    from stages.layout_review_stage import proposals_for
    pagemap = _pagemap(widows=(5,), refs={5: {"widowRefs": ["p-a"]}})
    once = proposals_for("widow", 5, pagemap, {"p-a": None})
    assert once == proposals_for("widow", 5, pagemap, {"p-a": None})
    assert once[0]["id"] != proposals_for("widow", 5, pagemap, {"p-a": "tight"})[0]["id"]
    assert proposals_for("runt", 5, pagemap, {"p-a": None}) == []        # the ref is the widow's


def test_each_applied_fit_is_cleared_still_defective_or_unmeasured():
    """K5: read off this build's pagemap. A paragraph it never names is no claim."""
    from stages.layout_review_stage import fit_outcomes
    pagemap = _pagemap(widows=(5,), refs={
        4: {"paraRanges": [{"paraIndex": 0, "linesOnPage": 9, "ref": "p-a"}, {"paraIndex": 1, "linesOnPage": 3, "ref": "p-b"}]},
        5: {"widowRefs": ["p-a"], "paraRanges": [{"paraIndex": 0, "linesOnPage": 1, "ref": "p-a"}]}})
    assert fit_outcomes(pagemap, {"p-a": "tight", "p-b": "tighter", "p-c": "tight", "p-d": None}) == [
        {"docxId": "p-a", "fit": "tight", "outcome": "still-defective"},
        {"docxId": "p-b", "fit": "tighter", "outcome": "cleared"},
        {"docxId": "p-c", "fit": "tight", "outcome": "unmeasured"}]


def test_a_fix_that_cleared_every_warning_is_still_reported(tmp_path, monkeypatch):
    """The fix worked, so preflight warned on nothing and no Compositor is built --
    and that is exactly the build whose outcome a reviewer needs to see."""
    monkeypatch.setattr(layout_review_stage, "_compositor", lambda api_key: pytest.fail("nothing to look at"))
    pagemap = _pagemap(refs={5: {"paraRanges": [{"paraIndex": 0, "linesOnPage": 4, "ref": "p-a"}]}})
    result, document = _run(tmp_path, _report(), pagemap, _doc(**{"p-a": "tight"}))
    assert document["findings"] == []
    assert document["fitOutcomes"] == [{"docxId": "p-a", "fit": "tight", "outcome": "cleared"}]
    assert result.metrics["fit_cleared"] == 1


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
