"""
The worker stores what each stage reported (docs/WIRING_PLAN.md W0).

A completed stage's warnings, and a failed stage's diagnostics, go into
`build_stages.diagnostics` (migration 007), where GET /v1/builds/:id reads
them. No database here: a fake connection records the statement and its
parameters, so the wiring itself is what's under test.
"""

from __future__ import annotations

import worker
from publisher_stages import Diagnostic

WARNING = Diagnostic(code="override-op-inapplicable", severity="warning",
                     human_message="override 'ov-1' (merge) was not applied: no chapter precedes it",
                     source_ref="c1")


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.statements.append((sql, params))


class _Conn:
    def __init__(self):
        self.statements = []

    def cursor(self, **_kwargs):
        return _Cursor(self)

    def commit(self):
        pass


def _stored_diagnostics(conn):
    sql, params = next((s, p) for s, p in conn.statements if "INSERT INTO build_stages" in s)
    assert "diagnostics = EXCLUDED.diagnostics" in sql, "a re-run must refresh them, not keep the old"
    return params[8].adapted


def test_a_completed_stage_stores_its_warnings():
    conn = _Conn()
    worker._record_stage(conn, "b1", "t1", "resolve", 4, "completed", False, 10,
                         {"overrides_inapplicable": 1}, [WARNING])
    assert _stored_diagnostics(conn) == [WARNING.to_dict()]


def test_a_stage_that_reported_nothing_stores_an_empty_list_not_null():
    """Null is reserved for a row written before diagnostics were kept."""
    conn = _Conn()
    worker._record_stage(conn, "b1", "t1", "extract", 7, "completed", True, 1, {})
    assert _stored_diagnostics(conn) == []
