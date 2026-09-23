"""
The worker hands a manuscript's override log to `resolve`.

PATCH /v1/documents/:id/overrides stores ops (f320c6b) and `resolve` reads their
shape (3bfac5c), but nothing carried one to the other: the worker never supplied
`resolve`'s `overrides_path`, so every build ran as if no reviewer had touched
the book. No database here -- a fake connection answers the two queries
`_initial_inputs_for` makes, so the wiring itself is what's under test. The
Postgres side (the grant, RLS) is migration 005 plus tests/integration.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import worker
from publisher_structure.overrides import parse_overrides

OP = {
    "id": "ov-1", "sourceRef": {"docxId": "p1"}, "op": "retitle", "value": "Two",
    "actor": "user:reviewer", "at": "2026-01-01T00:00:00Z",
}
SOURCE = b"PK fake docx bytes"


class _Cursor:
    def __init__(self, conn):
        self._conn, self._sql = conn, ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sql = sql

    def fetchone(self):
        assert "FROM manuscripts" in self._sql
        return {"source_sha256": hashlib.sha256(SOURCE).hexdigest()}

    def fetchall(self):
        assert "FROM override_ops" in self._sql and "ORDER BY seq" in self._sql
        return [(op,) for op in self._conn.ops]


class _Conn:
    def __init__(self, ops):
        self.ops = ops

    def cursor(self, **_kwargs):
        return _Cursor(self)


@pytest.fixture
def cas(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "CAS_ROOT", tmp_path)
    digest = hashlib.sha256(SOURCE).hexdigest()
    blob = tmp_path / digest[:2] / digest[2:4] / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(SOURCE)
    return tmp_path


def _inputs(ops):
    build = {"document_id": "ms-1", "profile_ids": ["Generic 6x9"]}
    registry = worker.build_registry(worker._registry_config_from_env())
    return worker._initial_inputs_for(_Conn(ops), build, registry)


def test_a_logged_op_reaches_resolve_as_an_overrides1_document(cas):
    second = {**OP, "id": "ov-2", "value": "Three"}
    path = Path(_inputs([OP, second])["resolve"]["overrides_path"])

    assert path.is_file() and path.is_relative_to(cas), "written into the CAS, not a temp file"
    assert path.name == hashlib.sha256(path.read_bytes()).hexdigest(), "content-addressed"
    ops = parse_overrides(json.loads(path.read_bytes()))   # resolve's own parser
    assert [op.id for op in ops] == ["ov-1", "ov-2"], "seq order, as the API received them"


def test_no_ops_means_no_overrides_input(cas):
    """Absent, not an empty document: `resolve`'s optional root input already
    means zero overrides, and an unreviewed book keeps the cache key it had."""
    assert "resolve" not in _inputs([])


def test_the_same_log_is_the_same_blob(cas):
    """JSONB does not keep key order, so two reads of one log can come back
    differently ordered. The bytes -- and so resolve's cache key -- must not."""
    reordered = dict(reversed(list(OP.items())))
    assert _inputs([OP])["resolve"] == _inputs([reordered])["resolve"]
