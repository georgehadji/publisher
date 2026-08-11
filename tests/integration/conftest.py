"""
Shared fixtures for the DB-backed integration tests (U1 worker durability,
U2 real manuscripts, and the existing A0 pipeline detectors).

These tests need a real Postgres and, for the worker paths, a writable CAS root.
They follow the repo convention (see test_api_drives_pipeline.py): DATABASE_URL
defaults to the compose-published port 55432 and is overridable via env. The
worker subprocess fixtures here use the same DATABASE_URL so the queue the test
writes to is the queue the worker drains.

Rows created by these tests are namespaced with a 'test-' id prefix and cleaned
up at session start, so reruns and parallel sessions do not fight over state.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

import psycopg2
import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
for sub in ("platform/stages/py", "platform/cas/py", "platform/cache/py"):
    if sub not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / sub))

API_DIR = REPO_ROOT / "packages" / "api"

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://publisher:publisher@localhost:55432/publisher"
)
TOKEN = "detector-token"
TENANT = "detector-tenant"
# Idempotency-Key is scoped (tenant, key) and rows persist in Postgres across
# test runs -- a literal key reused run-to-run would replay a PREVIOUS run's
# cached response instead of exercising the route again. Unique per process.
RUN_ID = uuid.uuid4().hex[:8]

TEST_CAS_ROOT = REPO_ROOT / ".test-cas-cache"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def db_url() -> str:
    return DATABASE_URL


@pytest.fixture(scope="session")
def cas_root() -> Path:
    TEST_CAS_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_CAS_ROOT


@pytest.fixture()
def db(db_url):
    """A connection to the real test Postgres, with leftover test rows purged."""
    conn = psycopg2.connect(db_url)
    _purge_test_rows(conn)
    yield conn
    _purge_test_rows(conn)
    conn.close()


def _purge_test_rows(conn) -> None:
    """Remove every row these tests may have created (ids start with 'test-')."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM artifacts WHERE build_id LIKE 'test-%'")
        cur.execute("DELETE FROM build_stages WHERE build_id LIKE 'test-%'")
        cur.execute("DELETE FROM builds WHERE id LIKE 'test-%'")
        cur.execute("DELETE FROM manuscripts WHERE id LIKE 'test-%'")
        cur.execute("DELETE FROM titles WHERE id LIKE 'test-%'")
        cur.execute("DELETE FROM idempotency_keys WHERE tenant_id LIKE 'test-tenant-%'")
    conn.commit()


def _cas_path(cas_root: Path, sha256: str) -> Path:
    return cas_root / sha256[:2] / sha256[2:4] / sha256


def write_to_cas(cas_root: Path, data: bytes) -> str:
    """Write bytes into the CAS layout the worker and API both use; return sha256."""
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    dest = _cas_path(cas_root, sha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return sha


@pytest.fixture()
def make_docx():
    """Build a minimal-but-valid DOCX in memory (a heading + one paragraph).

    `docx_to_ast` needs at least one recognised heading and some prose; a
    Heading 1 style plus a plain paragraph clears both bars regardless of
    casing.
    """

    def _make(heading: str, body: str) -> bytes:
        import io

        from docx import Document

        doc = Document()
        doc.add_heading(heading, level=1)
        doc.add_paragraph(body)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    return _make


def _register_tenant(conn, tenant: str) -> str:
    """Seed a tenant + title + manuscript row directly (no API needed)."""
    with conn.cursor() as cur:
        title_id = f"test-title-{RUN_ID}-{tenant}"
        ms_id = f"test-ms-{RUN_ID}-{tenant}"
        cur.execute(
            "INSERT INTO titles (id, tenant_id, title) VALUES (%s, %s, %s)",
            (title_id, tenant, f"Test title {tenant}"),
        )
        cur.execute(
            "INSERT INTO manuscripts (id, tenant_id, title_id) VALUES (%s, %s, %s)",
            (ms_id, tenant, title_id),
        )
    conn.commit()
    return ms_id


@pytest.fixture()
def register_tenant(db):
    def _register(tenant: str) -> str:
        return _register_tenant(db, tenant)

    return _register


def insert_build(conn, build_id: str, document_id: str, tenant: str, status: str = "queued",
                 *, attempt: int = 0, lease_age_s: int | None = None,
                 worker_id: str | None = None, profile_ids: str = '["Generic 6x9"]') -> None:
    """Insert a build row directly (no API needed)."""
    with conn.cursor() as cur:
        if status == "running":
            cur.execute(
                """
                INSERT INTO builds (id, tenant_id, document_id, design_id, profile_ids, mode, status,
                                    attempt, lease_expires_at, worker_id)
                VALUES (%s, %s, %s, %s, %s, 'proof', 'running', %s,
                        now() - %s * interval '1 second', %s)
                """,
                (build_id, tenant, document_id, None, profile_ids, attempt,
                 lease_age_s if lease_age_s is not None else 60, worker_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO builds (id, tenant_id, document_id, design_id, profile_ids, mode, status)
                VALUES (%s, %s, %s, %s, %s, 'proof', 'queued')
                """,
                (build_id, tenant, document_id, None, profile_ids),
            )
    conn.commit()


def set_manuscript_source(conn, manuscript_id: str, data: bytes) -> str:
    """Store DOCX bytes in CAS and point the manuscript at them; return sha256."""
    sha = write_to_cas(TEST_CAS_ROOT, data)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE manuscripts SET source_sha256 = %s, source_size = %s WHERE id = %s",
            (sha, len(data), manuscript_id),
        )
    conn.commit()
    return sha


@pytest.fixture(scope="module")
def worker_module():
    """worker.py imported AFTER the CAS root env is pinned (it reads it at import).

    Env is set for the whole test process, so in-process run_build() calls see
    the same CAS root and DATABASE_URL the subprocess worker would.
    """
    os.environ["PUBLISHER_CAS_ROOT"] = str(TEST_CAS_ROOT)
    os.environ["PUBLISHER_WORKER_LEASE_SECONDS"] = "60"
    os.environ["PUBLISHER_WORKER_MAX_ATTEMPTS"] = "3"
    os.environ.setdefault("DATABASE_URL", DATABASE_URL)
    sys.path.insert(0, str(REPO_ROOT))
    import worker

    return worker


def _headers(idem: str) -> dict:
    """Standard auth + Idempotency-Key headers for the shared test token."""
    return {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": f"{RUN_ID}-{idem}"}


@pytest.fixture(scope="module")
def api_server():
    """
    Boots the real Fastify server (not a mock) so tests exercise the actual
    routes in packages/api/src/index.ts, not a stand-in for them.
    """
    tsx = API_DIR / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
    if not tsx.exists():
        pytest.fail(
            f"tsx not found at {tsx} -- run `npm install` in packages/api "
            f"before this detector can even attempt to run."
        )

    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "PUBLISHER_API_TOKENS": f"{TOKEN}:{TENANT}",
        "PUBLISHER_CORS_ORIGINS": "http://localhost",
        "DATABASE_URL": DATABASE_URL,
        "PUBLISHER_CAS_ROOT": str(TEST_CAS_ROOT),
        # Small cap so the 413 (upload too large) path is testable with cheap bytes.
        "PUBLISHER_UPLOAD_MAX_BYTES": "100000",
        # U7 metrics endpoint gate (PUBLISHER_ADMIN_TOKENS unset => route absent).
        "PUBLISHER_ADMIN_TOKENS": "admin-secret",
    }
    proc = subprocess.Popen(
        [str(tsx), "src/index.ts"],
        cwd=str(API_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        healthy = False
        for _ in range(100):
            try:
                if requests.get(f"{base_url}/v1/health", timeout=1).status_code == 200:
                    healthy = True
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        if not healthy:
            proc.terminate()
            out = proc.stdout.read() if proc.stdout else ""
            pytest.fail(f"API server never became healthy.\n{out}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
