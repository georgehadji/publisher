"""
`epub` and `onix` stages (E7.2, docs/ARCHITECTURE_SCORE_10_PLAN.md).

Unlike `idml`, neither is gated behind an opt-in import: both need no
external toolchain, so they register unconditionally and are reachable in
every ordinary build the moment `resolve` is. The reachability tests below
are what proves that -- not just that the stage functions exist.
"""

from __future__ import annotations
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import stages  # noqa: F401 -- registers every stage, including epub/onix
from publisher_stages import StageCtx, StageError, ErrorKind, RegistryConfig, build_registry
from publisher_exec import plan
from stages.secondary_output_stages import epub, onix


def _selected_registry():
    """A properly SELECTED registry -- worker.py's own production shape. See
    stages/tests/test_structure_infer.py for why the raw get_registry() is
    the wrong tool here (ingest/acquire, design-compile/-typst are
    genuinely ambiguous without a selection)."""
    return build_registry(RegistryConfig(ingest_impl="ingest"))


def _ctx(tmp_path: Path) -> StageCtx:
    return StageCtx(
        build_id="test-secondary",
        deterministic_seed="test-secondary",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=128,
        work_dir=str(tmp_path),
        cas_root=str(tmp_path / "cas"),
    )


DOC = {
    "schema": "doc-effective/1",
    "metadata": {"title": "Test Book", "isbn": "9781234567890", "language": "en-US",
                 "contributors": [{"role": "author", "displayName": "Author Name"}]},
    "frontMatter": [],
    "body": [
        {"type": "chapter", "attrs": {"id": "ch1", "number": 1, "title": "One"},
         "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Words."}]}]},
        {"type": "chapter", "attrs": {"id": "ch2", "number": 2, "title": "Two"},
         "content": [{"type": "paragraph", "content": [{"type": "text", "text": "More words."}]}]},
    ],
    "backMatter": [],
}


def _doc_path(tmp_path: Path) -> Path:
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(DOC), encoding="utf-8")
    return path


# ── reachability: both are always-on, unlike the gated idml/structure-infer ──

def test_epub_and_onix_are_reachable_in_an_ordinary_build():
    reachable = plan(_selected_registry(), {"ingest": {"docx_path": "/fake/manuscript.docx"}}).order
    assert "epub" in reachable
    assert "onix" in reachable
    # Reachable BECAUSE resolve is -- not vacuously true.
    assert "resolve" in reachable


# ── epub ──────────────────────────────────────────────────────────────────

def test_epub_stage_writes_a_valid_package(tmp_path):
    ctx = _ctx(tmp_path)
    result = epub(ctx, doc_path=str(_doc_path(tmp_path)))

    assert len(result.artifacts) == 1
    art = result.artifacts[0]
    assert art.kind == "epub"
    assert art.media_type == "application/epub+zip"

    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    with zipfile.ZipFile(stored) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        assert zf.read("mimetype").decode() == "application/epub+zip"
        sections = [n for n in names if "sections/" in n]
        assert len(sections) == 2

    assert result.metrics["chapters"] == 2.0


def test_epub_stage_refuses_a_document_that_never_reached_resolve(tmp_path):
    with pytest.raises(StageError) as exc:
        epub(_ctx(tmp_path), doc_path=None)
    assert exc.value.kind == ErrorKind.BAD_INPUT


# ── onix ──────────────────────────────────────────────────────────────────

def test_onix_stage_writes_valid_metadata(tmp_path):
    ctx = _ctx(tmp_path)
    result = onix(ctx, doc_path=str(_doc_path(tmp_path)))

    assert len(result.artifacts) == 1
    art = result.artifacts[0]
    assert art.kind == "onix"
    assert art.media_type == "application/xml"

    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    content = stored.read_text(encoding="utf-8")
    assert "ONIXMessage" in content
    assert "3.0" in content
    assert "Test Book" in content


def test_onix_stage_refuses_a_document_that_never_reached_resolve(tmp_path):
    with pytest.raises(StageError) as exc:
        onix(_ctx(tmp_path), doc_path=None)
    assert exc.value.kind == ErrorKind.BAD_INPUT
