"""
`manuscript-advisory` stage (E7.2, docs/ARCHITECTURE_SCORE_10_PLAN.md).

Wires services/alttext's ManuscriptDoctor onto a real, reachable path. The
reachability tests are the actual proof this is "advisory only": there is no
edge from `advisory-report/1` to anything, so nothing downstream can even
see this stage's output, let alone be shaped by it.
"""

from __future__ import annotations
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import stages  # noqa: F401 -- registers every stage, including manuscript-advisory
from publisher_stages import StageCtx, StageError, ErrorKind, RegistryConfig, build_registry
from publisher_exec import plan
from stages.advisory_stage import manuscript_advisory

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _selected_registry():
    return build_registry(RegistryConfig(ingest_impl="ingest"))


def _ctx(tmp_path: Path) -> StageCtx:
    return StageCtx(
        build_id="test-advisory",
        deterministic_seed="test-advisory",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=64,
        work_dir=str(tmp_path),
        cas_root=str(tmp_path / "cas"),
    )


def _fake_docx(tmp_path: Path, name: str = "upload.bin") -> Path:
    """A minimal real ZIP (what a CAS-stored, extension-free DOCX blob
    actually is) -- exercises the magic-byte sniff, not a client-claimed name."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", "<document/>")
    return path


# ── reachability ──────────────────────────────────────────────────────────

def test_advisory_is_unreachable_without_its_own_root_input():
    reachable = plan(_selected_registry(), {"ingest": {"docx_path": "/fake/manuscript.docx"}}).order
    assert "manuscript-advisory" not in reachable


def test_advisory_is_reachable_once_supplied():
    reachable = plan(_selected_registry(), {
        "ingest": {"docx_path": "/fake/manuscript.docx"},
        "manuscript-advisory": {"docx_path": "/fake/manuscript.docx"},
    }).order
    assert "manuscript-advisory" in reachable


# ── stage behaviour ───────────────────────────────────────────────────────

def test_advisory_stage_writes_a_report_for_a_real_docx(tmp_path):
    ctx = _ctx(tmp_path)
    docx = _fake_docx(tmp_path)

    result = manuscript_advisory(ctx, docx_path=str(docx))

    assert len(result.artifacts) == 1
    art = result.artifacts[0]
    assert art.kind == "report"
    assert art.media_type == "application/json"

    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    report = json.loads(stored.read_text(encoding="utf-8"))
    assert report["status"] == "success"
    # The extension-free CAS path must have been sniffed as a ZIP/DOCX, not
    # left as "" (which ManuscriptDoctor reports as unsupported-format).
    assert report["extension"] == ".docx"


def test_advisory_stage_sniffs_legacy_doc_by_magic_bytes(tmp_path):
    ctx = _ctx(tmp_path)
    docx = tmp_path / "upload.bin"
    docx.write_bytes(_OLE2_MAGIC + b"rest of a legacy .doc file")

    result = manuscript_advisory(ctx, docx_path=str(docx))
    stored_hash = result.artifacts[0].hash
    stored = Path(ctx.cas_root) / stored_hash[:2] / stored_hash[2:4] / stored_hash
    report = json.loads(stored.read_text(encoding="utf-8"))
    assert report["extension"] == ".doc"
    assert any(f["code"] == "legacy-format" for f in report["findings"])


def test_advisory_stage_requires_docx_path(tmp_path):
    with pytest.raises(StageError) as exc:
        manuscript_advisory(_ctx(tmp_path), docx_path=None)
    assert exc.value.kind == ErrorKind.BAD_INPUT


def test_advisory_stage_refuses_a_missing_file(tmp_path):
    with pytest.raises(StageError) as exc:
        manuscript_advisory(_ctx(tmp_path), docx_path=str(tmp_path / "nope.bin"))
    assert exc.value.kind == ErrorKind.BAD_INPUT
