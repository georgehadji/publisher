"""
U1 -- worker durability (docs/ARCHITECTURE_UPLIFT_PLAN.md §3, U1).

Gates (must FAIL against pre-U1 code):

1. A build left at 'running' by a killed worker is reclaimed once its lease
   expires. Pre-U1: the claim query selects only status='queued', so the row
   hangs at 'running' forever.
2. A build that has exhausted its attempts dead-letters to 'dead' instead of
   looping. Pre-U1: no attempt/lease columns exist at all.
3. A stage raising a non-StageError (e.g. ValueError) leaves status='failed'
   and error_kind='INTERNAL' before the exception is re-raised. Pre-U1:
   run_build catches only StageError, so the row is left at 'running' and the
   process dies without recording anything.
4. SIGTERM stops the worker cleanly (exit 0) instead of orphaning in-flight
   work for a full lease period. Pre-U1: default handler, non-zero exit.

The full 'SIGKILL mid-stage -> second worker reclaims -> completed' scenario
needs rendering engines (weasyprint + Ghostscript) to reach 'completed'; on a
host without them the reclaimed build reaches 'failed' at the paginate engine
stage instead. The assertion here is the mechanism: the stuck row is reclaimed
(attempt incremented, worker_id replaced, lease renewed) and reaches a TERMINAL
state. That is what pre-U1 code cannot do.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

from conftest import RUN_ID, TEST_CAS_ROOT, insert_build, set_manuscript_source
# conftest puts tests/ on sys.path, and pytest imports it before this module.
from proc_control import kill_tree, new_session_kwargs

REPO_ROOT = Path(__file__).resolve().parents[2]


def _spawn_worker(db_url: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "DATABASE_URL": db_url,
        "PUBLISHER_CAS_ROOT": str(TEST_CAS_ROOT),
    }
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "worker.py")],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Its own session, so teardown can reach a Ghostscript/Typst child that
        # the worker had running. send_signal() below still targets this pid.
        **new_session_kwargs(),
    )


def _wait_for_terminal(db, build_id: str, timeout_s: int = 90) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM builds WHERE id = %s", (build_id,))
            row = cur.fetchone()
        if row and row["status"] in ("completed", "failed", "dead"):
            return dict(row)
        time.sleep(0.5)
    raise AssertionError(f"build {build_id} never reached a terminal state: {dict(row) if row else None}")


def test_expired_running_lease_is_reclaimed(db, db_url, make_docx, register_tenant):
    """Gate 1 -- a 'running' row with an expired lease is claimed and finished."""
    ms_id = register_tenant("u1-reclaim")
    docx = make_docx("RECLAIM ME", "A short body paragraph for the reclaim test build.")
    set_manuscript_source(db, ms_id, docx)

    build_id = f"test-{RUN_ID}-reclaim"
    insert_build(db, build_id, ms_id, "test-tenant-u1-reclaim",
                 status="running", attempt=0, lease_age_s=60, worker_id="dead-worker")

    proc = _spawn_worker(db_url)
    try:
        row = _wait_for_terminal(db, build_id)
    finally:
        kill_tree(proc)

    assert row["status"] in ("completed", "failed"), (
        f"reclaimed build must reach a terminal state, got {row['status']}"
    )
    assert row["attempt"] >= 1, "reclaimed build must have attempt incremented"
    assert row["worker_id"] != "dead-worker", "a live worker must have taken over the lease"
    assert row["lease_expires_at"] is not None and row["lease_expires_at"] > datetime.now(timezone.utc), (
        "the reclaiming worker must hold a fresh lease"
    )


def test_poison_build_dead_letters_after_max_attempts(db, worker_module, register_tenant):
    """Gate 2 -- attempt exhaustion moves a build to 'dead' instead of looping."""
    ms_id = register_tenant("u1-deadletter")
    build_id = f"test-{RUN_ID}-deadletter"
    insert_build(db, build_id, ms_id, "test-tenant-u1-deadletter",
                 status="running", attempt=worker_module.MAX_ATTEMPTS,
                 lease_age_s=60, worker_id="tired-worker")

    claimed = worker_module._claim_build(db)
    assert claimed is None, "an exhausted build must NOT be claimed"

    with db.cursor() as cur:
        cur.execute("SELECT status, error_kind FROM builds WHERE id = %s", (build_id,))
        row = cur.fetchone()
    assert row[0] == "dead", f"exhausted build must dead-letter, got status {row[0]}"
    assert row[1] == "exhausted"


def test_unexpected_exception_records_failed_then_reraises(db, worker_module, monkeypatch,
                                                           make_docx, register_tenant):
    """Gate 3 -- a non-StageError failure is recorded as 'failed' before re-raise."""
    ms_id = register_tenant("u1-crash")
    docx = make_docx("CRASH TEST", "Body text for the crash test build.")
    set_manuscript_source(db, ms_id, docx)

    build_id = f"test-{RUN_ID}-crash"
    insert_build(db, build_id, ms_id, "test-tenant-u1-crash")

    def boom(*args, **kwargs):
        raise ValueError("simulated engine bug")

    monkeypatch.setattr(worker_module.DagExecutor, "execute", boom)

    with pytest.raises(ValueError, match="simulated engine bug"):
        worker_module.run_build(db, {"id": build_id, "document_id": ms_id,
                                     "profile_ids": ["Generic 6x9"]})

    with db.cursor() as cur:
        cur.execute("SELECT status, error_kind, error_message FROM builds WHERE id = %s", (build_id,))
        status, error_kind, error_message = cur.fetchone()
    assert status == "failed", f"build must be recorded 'failed', got '{status}'"
    assert error_kind == "INTERNAL", f"error_kind must be INTERNAL, got '{error_kind}'"
    assert "ValueError" in (error_message or "")


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot deliver SIGTERM to a subprocess")
def test_sigterm_stops_worker_cleanly(db, db_url):
    """Gate 4 -- SIGTERM -> finish current work, claim nothing new, exit 0."""
    proc = _spawn_worker(db_url)
    try:
        # Give it a moment to boot and reach the poll loop.
        time.sleep(2.5)
        assert proc.poll() is None, "worker exited before SIGTERM"
        proc.send_signal(signal.SIGTERM)
        code = proc.wait(timeout=10)
    finally:
        # Safety net only -- the assertion below is the point of this test, so
        # the graceful SIGTERM path above is left exactly as it was.
        kill_tree(proc)
    assert code == 0, f"worker must exit 0 on SIGTERM, got {code}"
