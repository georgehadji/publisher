"""
The Compositor's fix loop end to end on a Word-authored book (COMPOSITOR_PATCH_PLAN
K0-K5): render, preflight warns, layout-review proposes a `fit`, a reviewer accepts
it into the override log, the next build applies it and reports what it did.

A proof of the MECHANICS, not that the house design ever triggers the loop: under
the house stylesheet `corpus/word/word-composition.docx` widows nowhere (`widows: 2`
forbids it; measured 2026-10-15 over ~1,100 pages of prose, notes, headings, tables,
roles and marks, in GFS Didot and DejaVu Serif). So this renders the house
stylesheet with that one rule relaxed, and only that rule.

Real stages throughout -- ingest, extract, ast-assemble (the integrity gate),
resolve, paginate, preflight's composition check, layout-review -- with one stand-in:
the Compositor's model, here a provider that confirms every widow it is shown.
Where a line breaks is the face's business, so nothing below names a paragraph or
a page; it asserts what holds on any face.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import stages  # noqa: F401 -- registers every stage
import stages.layout_review_stage as layout_review_stage
from publisher_stages import StageCtx
from stages.text_stream import ast_text

ROOT = Path(__file__).resolve().parents[2]
BOOK = ROOT / "corpus/word/word-composition.docx"
MAX_ROUNDS = 3   # as designed -> tight -> tighter: two accepts, three builds


class ConfirmingCompositor:
    """The Compositor's model, stood in for: confirms every widow and runt it is shown."""

    def respond(self, role, messages, tools, output_schema):
        from publisher_agents.runtime import AgentTurn
        shown = json.loads(messages[1]["content"])["findings"]
        return AgentTurn(answer={"verdicts": [
            {"finding": f["id"], "verdict": "confirmed" if f["kind"] in ("widow", "runt") else "not-a-defect"}
            for f in shown]}, tokens=10)


def _ctx(tmp_path: Path) -> StageCtx:
    return StageCtx(build_id="fit-loop", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                    memory_budget_mb=1024, work_dir=str(tmp_path / "work"), cas_root=str(tmp_path / "cas"))


def _out(ctx: StageCtx, result, kind: str) -> str:
    digest = next(a.hash for a in result.artifacts if a.kind == kind)
    return str(next(Path(ctx.cas_root).rglob(digest)))


def _stylesheet(tmp_path: Path) -> str:
    """The house stylesheet with `widows: 2` relaxed to 1: the one rule that keeps
    this book clean. Orphans stay forbidden -- one fails preflight."""
    from stages.rendering import emit_css
    from templates import house_designspec
    house = emit_css(house_designspec())
    relaxed = house.replace("widows: 2;", "widows: 1;")
    assert relaxed != house, "the house stylesheet no longer sets widows: 2 -- revisit this test"
    path = tmp_path / "relaxed.css"
    path.write_text(relaxed, encoding="utf-8")
    return str(path)


def _accept(proposals: list[dict], at: str) -> list[dict]:
    """What the API's accept route logs for each (manuscripts.ts proposalOp), from the
    tables tests/test_single_source.py pins it to."""
    from stages.propose_stage import PROPOSAL_OPS, PROPOSAL_PATHS
    return [{"id": f"ov-{p['id']}", "sourceRef": {"docxId": p["docxId"]}, "op": PROPOSAL_OPS[p["type"]],
             "path": PROPOSAL_PATHS[p["type"]], "value": p["value"], "actor": "user:reviewer", "at": at}
            for p in proposals]


def test_an_accepted_fit_is_applied_by_the_next_build_and_its_outcome_reported(tmp_path, monkeypatch):
    pytest.importorskip("weasyprint")
    from publisher_agents.compositor import Compositor
    from publisher_agents.runtime import AgentRuntime
    from publisher_prepress.preflight import check_composition
    from stages.ingest_stage import ingest
    from stages.extract_stage import extract
    from stages.structure_stage import ast_assemble
    from stages.resolve_stage import resolve
    from stages.paginate_stage import paginate
    from stages.layout_review_stage import layout_review

    monkeypatch.setattr(layout_review_stage, "_compositor",
                        lambda api_key: Compositor(AgentRuntime(ConfirmingCompositor())))
    ctx = _ctx(tmp_path)
    source = _out(ctx, ingest(ctx, docx_path=str(BOOK)), "source")
    ast = _out(ctx, ast_assemble(ctx, html=_out(ctx, extract(ctx, source=source), "html"), source=source), "ast")
    css = _stylesheet(tmp_path)

    log: list[dict] = []
    accepted: dict[str, str] = {}          # docxId -> the fit last accepted for it
    outcomes: dict[str, list[str]] = {}    # docxId -> its outcome in each later build
    for build in range(MAX_ROUNDS):
        overrides = tmp_path / f"overrides-{build}.json"
        overrides.write_text(json.dumps({"schema": "overrides/1", "ops": log}), encoding="utf-8")
        doc = _out(ctx, resolve(ctx, ast=ast, overrides_path=str(overrides)), "doc")
        rendered = paginate(ctx, doc_path=doc, css_path=css)
        pagemap = json.loads(Path(_out(ctx, rendered, "pagemap")).read_bytes())

        check = check_composition({"pagemap": pagemap}, {})
        assert check.status in ("warn", "pass"), check   # never an orphan: the stylesheet still forbids them
        report = tmp_path / f"report-{build}.json"
        report.write_text(json.dumps({"schema": "preflight/1", "status": check.status, "checks": [
            {"code": check.code, "status": check.status, "severity": "warning", "humanMessage": "x"}]}),
            encoding="utf-8")
        review = layout_review(ctx, report=str(report), pdf_path=_out(ctx, rendered, "pdf"),
                               pagemap_path=_out(ctx, rendered, "pagemap"), doc_path=doc, api_key="sk-scripted")
        document = json.loads(Path(_out(ctx, review, "review")).read_bytes())

        # Each fit the log carries is reported, against the value this build applied.
        reported = {o["docxId"]: o for o in document.get("fitOutcomes", [])}
        assert set(reported) == set(accepted), "a fit was applied and not reported, or reported and not applied"
        for docx_id, fit in accepted.items():
            assert reported[docx_id]["fit"] == fit
            assert reported[docx_id]["outcome"] in ("cleared", "still-defective")   # the CSS path names them
            outcomes.setdefault(docx_id, []).append(reported[docx_id]["outcome"])

        # The fix never touched a word: the effective document reads as the AST does.
        assert ast_text(json.loads(Path(doc).read_bytes())) == ast_text(json.loads(Path(ast).read_bytes()))

        proposals = [p for f in document["findings"] for p in f.get("proposals", [])]
        if build == 0:
            assert proposals, "this book under the relaxed stylesheet must widow somewhere -- see corpus/word"
            assert {p["value"] for p in proposals} == {"tight"}   # nothing carried a fit yet
        fresh = {p["id"]: p for p in proposals if f"ov-{p['id']}" not in {op["id"] for op in log}}
        log += _accept(list(fresh.values()), f"2026-10-15T00:00:0{build}Z")
        accepted.update({p["docxId"]: p["value"] for p in fresh.values()})

    assert any("cleared" in seen for seen in outcomes.values()), (
        f"no accepted fit cleared its defect within {MAX_ROUNDS} builds: {outcomes}")
