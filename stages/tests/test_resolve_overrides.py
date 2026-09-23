"""
The `resolve` stage applies an `overrides/1` log in the shape the API stores.

Before v3 it built ops with `OverrideOp(**op)` and raised TypeError on the first
schema-valid one, so no log PATCH /v1/documents/:id/overrides accepts could
ever have reached a build.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from publisher_stages import ErrorKind, StageCtx, StageError
from stages.resolve_stage import resolve


def _ctx(tmp_path: Path) -> StageCtx:
    return StageCtx(
        build_id="test-resolve",
        deterministic_seed="test-resolve",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=128,
        work_dir=str(tmp_path),
        cas_root=str(tmp_path / "cas"),
    )


AST = {
    "schema": "ast/1",
    "body": [{
        "type": "chapter",
        "sourceRef": {"docxId": "p1"},
        "attrs": {"number": 1, "id": "ch1", "title": "One"},
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Prose."}]}],
    }],
}


def _write(tmp_path: Path, name: str, data) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _effective(ctx: StageCtx, result) -> dict:
    digest = result.artifacts[0].hash
    return json.loads((Path(ctx.cas_root) / digest[:2] / digest[2:4] / digest).read_bytes())


def test_resolve_applies_a_log_in_the_shape_the_api_stores(tmp_path):
    ctx = _ctx(tmp_path)
    log = {"schema": "overrides/1", "documentId": "ms-1", "astVersion": 1, "ops": [{
        "id": "ov-1", "sourceRef": {"docxId": "p1"}, "op": "retitle", "value": "Two",
        "actor": "user:reviewer", "at": "2026-01-01T00:00:00Z",
    }]}
    result = resolve(ctx, ast=_write(tmp_path, "ast.json", AST),
                     overrides_path=_write(tmp_path, "ov.json", log))
    assert _effective(ctx, result)["body"][0]["attrs"]["title"] == "Two"


@pytest.mark.parametrize("log", [
    # The dataclass's own shape -- what v2 read, and what nothing now writes.
    {"schema": "overrides/1", "ops": [{"id": "ov-1", "sourceRef": "p1", "op": "retitle",
                                       "value": "Two", "actor": "u",
                                       "created_at": "2026-01-01T00:00:00Z"}]},
    {"ops": []},   # no schema id: must not be read as "no reviewer touched this"
])
def test_resolve_refuses_a_malformed_log_as_bad_input(tmp_path, log):
    ctx = _ctx(tmp_path)
    with pytest.raises(StageError) as exc:
        resolve(ctx, ast=_write(tmp_path, "ast.json", AST),
                overrides_path=_write(tmp_path, "ov.json", log))
    assert exc.value.kind == ErrorKind.BAD_INPUT
    assert exc.value.diagnostics[0].code == "override-log-malformed"


def test_no_log_still_means_zero_overrides(tmp_path):
    ctx = _ctx(tmp_path)
    result = resolve(ctx, ast=_write(tmp_path, "ast.json", AST))
    assert _effective(ctx, result)["body"][0]["attrs"]["title"] == "One"
