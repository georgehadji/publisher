"""
A book preflight refuses still has a readable verdict.

The report was written to CAS and then the stage raised, and only a stage that
succeeds has its artifacts recorded -- so the first real book's rejection
(4 orphan lines) showed in GET /v1/builds/:id/preflight as "pending", forever.
The error now carries the report (StageError.artifacts) for the worker to record.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import stages.prepress_stages as prepress
from publisher_stages import ErrorKind, StageCtx, StageError


def _check(code: str, status: str, message: str):
    return SimpleNamespace(code=code, status=status, humanMessage=message,
                           suggestedFix=None, sourceRef=None)


def _report(status: str, checks: list):
    summary = {"passed": 9, "failed": sum(c.status == "fail" for c in checks),
               "warnings": sum(c.status == "warn" for c in checks)}
    return SimpleNamespace(status=status, summary=summary, checks=checks,
                           to_dict=lambda: {"status": status, "summary": summary,
                                            "checks": [vars(c) for c in checks]})


def _run(tmp_path: Path, monkeypatch, report):
    monkeypatch.setattr(prepress, "run_preflight", lambda *a, **k: report)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.3\n%%EOF")
    ctx = StageCtx(build_id="pf", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_path), cas_root=str(tmp_path / "cas"))
    return ctx, lambda: prepress.preflight_stage(ctx, pdf_path=str(pdf), profile_name="Greek 17x24")


def test_a_failed_verdict_is_carried_by_the_error(tmp_path, monkeypatch, capsys):
    orphans = _check("composition", "fail", "4 page(s) begin a paragraph with a single line")
    ctx, run = _run(tmp_path, monkeypatch, _report("fail", [orphans]))
    with pytest.raises(StageError) as exc:
        run()
    assert exc.value.kind == ErrorKind.POLICY_VIOLATION
    [art] = exc.value.artifacts
    assert art.kind == "report"
    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    assert json.loads(stored.read_bytes())["status"] == "fail"


def test_warnings_are_printed_not_only_counted(tmp_path, monkeypatch, capsys):
    notice = _check("fonts", "warn", "2 fallback faces fill missing glyphs")
    _, run = _run(tmp_path, monkeypatch, _report("pass", [notice]))
    result = run()
    assert "WARN fonts: 2 fallback faces fill missing glyphs" in capsys.readouterr().out
    assert result.artifacts[0].kind == "report"
