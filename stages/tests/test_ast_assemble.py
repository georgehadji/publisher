"""
Mutation test for the text-integrity gate -- BUILD_PLAN.md REMEDIATION_PLAN.md §6:
"Deleting one paragraph from a manuscript fails the build". This fixture is
permanent; never relax or remove it once passing.

Exercises the actual registered `ast-assemble` stage function against real files on
disk (via a StageCtx pointed at a temp work dir), not the underlying comparison
helpers directly -- those are already covered by
services/structure/tests/test_rules.py. This is the pipeline-level guarantee: the
stage a real build calls must reject a mutated manuscript, not just the library
function it happens to use internally.
"""

from __future__ import annotations
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from publisher_stages import StageCtx, StageError, ErrorKind
from stages.structure_stage import ast_assemble
from stages.extract_stage import _ast_to_html


GOOD_AST = {
    "schema": "ast/1",
    "metadata": {"title": "Test Book"},
    "frontMatter": [],
    "body": [
        {
            "type": "chapter",
            "attrs": {"number": 1, "title": "Chapter One", "id": "ch1", "startsOn": "recto"},
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "The first sentence."}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "The second sentence."}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "The third and final sentence."}]},
            ],
        },
    ],
    "backMatter": [],
}


def _write(tmp_dir: Path, name: str, obj) -> str:
    path = tmp_dir / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def _ctx(tmp_dir: Path) -> StageCtx:
    return StageCtx(
        build_id="test-build",
        cache_key="test",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=128,
        work_dir=str(tmp_dir),
        allow_stub_engines=True,
    )


def test_ast_assemble_passes_on_faithful_conversion():
    """Baseline: HTML honestly derived from the source AST must pass the gate."""
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        html = _ast_to_html(GOOD_AST)
        html_path = tmp_dir / "extract.html"
        html_path.write_text(html, encoding="utf-8")
        source_path = _write(tmp_dir, "source.json", GOOD_AST)

        result = ast_assemble(_ctx(tmp_dir), html=str(html_path), source=source_path)
        assert result.metrics["integrity_ok"] == 1.0


def test_ast_assemble_rejects_dropped_paragraph():
    """
    The gate's entire reason to exist. The HTML is missing the manuscript's third
    paragraph entirely (simulating a lossy extract/conversion step) -- the build
    MUST fail, not silently proceed with a shorter book than the author wrote.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        html = _ast_to_html(GOOD_AST)
        mutated_html = html.replace(
            '<p class="paragraph">The third and final sentence.</p>', ""
        )
        assert mutated_html != html, "mutation didn't take -- fixture text changed?"

        html_path = tmp_dir / "extract.html"
        html_path.write_text(mutated_html, encoding="utf-8")
        source_path = _write(tmp_dir, "source.json", GOOD_AST)

        with pytest.raises(StageError) as exc_info:
            ast_assemble(_ctx(tmp_dir), html=str(html_path), source=source_path)
        assert exc_info.value.kind == ErrorKind.ENGINE_BUG


def test_ast_assemble_requires_both_inputs():
    """No path through this stage can run with only one side of the comparison."""
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        with pytest.raises(StageError) as exc_info:
            ast_assemble(_ctx(tmp_dir), html="some/path.html", source=None)
        assert exc_info.value.kind == ErrorKind.BAD_INPUT

        with pytest.raises(StageError) as exc_info:
            ast_assemble(_ctx(tmp_dir), html=None, source="some/path.json")
        assert exc_info.value.kind == ErrorKind.BAD_INPUT
