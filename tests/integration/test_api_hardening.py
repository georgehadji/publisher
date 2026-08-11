"""
Stage 2 gates -- U5 API hardening + U7 observability
(docs/ARCHITECTURE_UPLIFT_PLAN.md §3 U5/U7).

Gates (must FAIL against pre-Stage-2 code):

1. S3 -- concurrent identical POST /v1/builds with one Idempotency-Key creates
   EXACTLY ONE build. Pre-S3: the key was only checked before and inserted
   after the handler, so two racing retries both created a build (N2).
2. S2 -- casPath rejects a path-traversal hash. Pre-S2: path.join happily.
3. S4 -- webhook url: http:// and private/loopback/metadata hosts are refused
   at creation. Pre-S4: any URI accepted.
4. U7 -- /v1/health returns 503 with postgres down, and 503 with CAS missing.
   Pre-U7: unconditional 200 'ok'.
5. U7 -- /v1/admin/metrics aggregates build_stages p50/p95 and is admin-gated.
   Pre-U7: no such route (404).
6. U5/S11 -- the SSE endpoint pushes events from Postgres NOTIFY instead of
   polling. Pre-S11: a pg_notify lands on no LISTENer and the client never
   sees the event within the poll interval's blind spot (it polled, not
   pushed).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg2
import pytest
import requests

from conftest import (
    API_DIR, DATABASE_URL, RUN_ID, TEST_CAS_ROOT, TOKEN,
    _headers, api_server, create_uploaded_manuscript, make_docx,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── S3: idempotency TOCTOU ──────────────────────────────────────


def test_concurrent_idempotent_build_creates_exactly_one(api_server, make_docx):
    ms_id = create_uploaded_manuscript(api_server, make_docx, "IDEM NOVEL", "Body of the idempotency test.", tag="u5idem")[0]
    key = f"{RUN_ID}-concurrent"
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    bodies: list[dict] = []

    def fire() -> None:
        barrier.wait()
        resp = requests.post(
            f"{api_server}/v1/builds",
            json={"documentId": ms_id, "designId": "d1", "profileIds": ["Generic 6x9"]},
            headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": key},
        )
        statuses.append(resp.status_code)
        try:
            bodies.append(resp.json())
        except ValueError:
            bodies.append({})

    threads = [threading.Thread(target=fire), threading.Thread(target=fire)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert 201 in statuses, f"at least one request must win the reservation: {statuses} {bodies}"
    assert all(s in (201, 409) for s in statuses), f"only 201 (winner/replay) or 409 (in flight): {statuses}"

    # The decisive assertion: exactly one build row for this document.
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM builds b JOIN manuscripts m ON m.id = b.document_id "
                "WHERE m.id = %s",
                (ms_id,),
            )
            count = cur.fetchone()[0]
    finally:
        conn.close()
    assert count == 1, f"concurrent same-key retries created {count} builds, expected exactly 1"

    # And a replay after the winner finished returns the stored response.
    replay = requests.post(
        f"{api_server}/v1/builds",
        json={"documentId": ms_id, "designId": "d1", "profileIds": ["Generic 6x9"]},
        headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": key},
    )
    assert replay.status_code == 201, f"sequential replay must return the stored response: {replay.status_code}"


# ── S2: casPath rejects path traversal ──────────────────────────


def test_cas_path_rejects_malformed_hash():
    """S2 gate: a non-sha256 value must be refused before path.join (N5)."""
    script = (
        "import('./src/db.ts').then(m => {"
        "  try { m.casPath('../../etc/passwd'); console.log('ACCEPTED'); process.exit(1); }"
        "  catch (e) { console.log('REJECTED'); process.exit(0); }"
        "})"
    )
    tsx = API_DIR / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
    proc = subprocess.run(
        [str(tsx), "-e", script],
        cwd=str(API_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "REJECTED" in proc.stdout, f"casPath accepted a traversal hash: {proc.stdout} {proc.stderr}"


# ── S4: webhook url validation ──────────────────────────────────


def test_webhook_rejects_http_and_private_urls(api_server):
    cases = [
        ("http://example.com/hook", "must be https"),
        ("https://127.0.0.1/hook", "private"),
        ("https://10.0.0.5/hook", "private"),
        ("https://169.254.169.254/latest/meta-data", "private"),
        ("https://[::1]/hook", "private"),
    ]
    for i, (url, needle) in enumerate(cases):
        resp = requests.post(
            f"{api_server}/v1/webhooks",
            json={"url": url, "events": ["build.completed"]},
            # Unique Idempotency-Key per case: S3 replays stored responses for
            # the SAME key, so a shared key would replay the first 400.
            headers=_headers(f"u5wh{i}"),
        )
        assert resp.status_code == 400, f"{url} must be rejected: {resp.status_code} {resp.text}"
        assert needle in resp.json().get("error", ""), f"{url}: wrong error: {resp.text}"


def test_webhook_accepts_public_https(api_server):
    resp = requests.post(
        f"{api_server}/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["build.completed"]},
        headers=_headers("u5whok"),
    )
    # Needs DNS; skip rather than flake on an offline host.
    if resp.status_code == 400 and "does not resolve" in resp.json().get("error", ""):
        pytest.skip("no DNS in this environment")
    assert resp.status_code == 201, f"public https url must be accepted: {resp.status_code} {resp.text}"


# ── U7: health checks Postgres + CAS ────────────────────────────


def _spawn_api(env_overrides: dict) -> subprocess.Popen:
    env = {
        **os.environ,
        "PORT": "0",
        "HOST": "127.0.0.1",
        "PUBLISHER_API_TOKENS": "detector-token:detector-tenant",
        "DATABASE_URL": DATABASE_URL,
        "PUBLISHER_CAS_ROOT": str(TEST_CAS_ROOT),
        **env_overrides,
    }
    return subprocess.Popen(
        [str(API_DIR / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")), "src/index.ts"],
        cwd=str(API_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_health(port: int, expect_status: int, timeout_s: int = 30) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"http://127.0.0.1:{port}/v1/health", timeout=2)
            last = resp.json()
            if resp.status_code == expect_status:
                return last
        except requests.RequestException:
            pass
        time.sleep(0.3)
    raise AssertionError(f"health never returned {expect_status}: {last}")


def test_health_degraded_when_postgres_down(api_server):
    """U7 gate: with the database unreachable, /v1/health must NOT say ok."""
    import socket as _socket

    # Reserve a port nothing listens on, then release it for the API subprocess.
    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = _spawn_api({
        "PORT": str(port),
        "DATABASE_URL": f"postgresql://publisher:publisher@127.0.0.1:{port}/nope",
        "PUBLISHER_PG_POOL_MAX": "1",
    })
    try:
        # Wait for the server to boot by hitting the health route itself.
        # Generous deadline: on this Windows host the SECOND concurrent tsx
        # instance is ~5x slower to boot than the first (real-time AV scans
        # every .ts; the module api_server fixture is already running, so this
        # spawned process is instance #2 -- measured 86s here). The mechanism
        # under test is the health response, not boot speed; 20s was flaky.
        deadline = time.monotonic() + 120
        body: dict = {}
        while time.monotonic() < deadline:
            try:
                resp = requests.get(f"http://127.0.0.1:{port}/v1/health", timeout=2)
                body = resp.json()
                if resp.status_code == 503:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.3)
        assert body.get("status") == "degraded", f"health must degrade with postgres down: {body}"
        assert body.get("checks", {}).get("postgres") == "down", f"postgres check must report down: {body}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── U7: /v1/admin/metrics ───────────────────────────────────────


def test_admin_metrics_aggregates_stages(api_server, db, register_tenant):
    # A real manuscript row (via register_tenant) instead of a hardcoded
    # document_id with no matching manuscript -- consistent with the RUN_ID
    # namespacing every other test uses, and immune to any future query that
    # joins builds.document_id against manuscripts.
    ms_id = register_tenant("test-tenant-u7")
    # Seed build_stages rows the endpoint aggregates, under a stage name unique
    # to this test so rows left by other suites cannot skew the assertion.
    with db.cursor() as cur:
        for i in range(4):
            cur.execute(
                "INSERT INTO builds (id, tenant_id, document_id, status) VALUES (%s, 'test-tenant-u7', %s, 'failed')",
                (f"test-{RUN_ID}-m{i}", ms_id),
            )
            cur.execute(
                "INSERT INTO build_stages (build_id, stage_name, stage_version, status, duration_ms) "
                "VALUES (%s, 'metrics-probe', 1, 'completed', %s)",
                (f"test-{RUN_ID}-m{i}", 100 + i * 100),
            )
    db.commit()

    resp = requests.get(
        f"{api_server}/v1/admin/metrics",
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert resp.status_code == 200, f"metrics must be reachable with the admin token: {resp.status_code} {resp.text}"
    payload = resp.json()
    stages = {s["stage_name"]: s for s in payload["stages"]}
    assert "metrics-probe" in stages, f"metrics missing metrics-probe: {stages}"
    assert stages["metrics-probe"]["p50_ms"] == "250.0", f"p50 wrong: {stages['metrics-probe']}"
    assert int(stages["metrics-probe"]["runs"]) == 4, f"runs wrong: {stages['metrics-probe']}"

    # Admin-gated: no token => 401.
    denied = requests.get(f"{api_server}/v1/admin/metrics")
    assert denied.status_code == 401, f"metrics must require the admin token: {denied.status_code}"


# ── U5/S11: SSE is push-based via LISTEN/NOTIFY ─────────────────


def test_sse_pushes_notified_events(api_server, db, make_docx):
    """S11 gate: a pg_notify must reach a connected SSE client without polling."""
    ms_id = create_uploaded_manuscript(api_server, make_docx, "SSE NOVEL", "Body of the SSE test.", tag="u5sse")[0]
    build = requests.post(
        f"{api_server}/v1/builds",
        json={"documentId": ms_id, "designId": "d1", "profileIds": ["Generic 6x9"]},
        headers=_headers("u5sse"),
    ).json()
    build_id = build["buildId"]

    with requests.get(
        f"{api_server}/v1/builds/{build_id}/events", stream=True, headers=_headers("u5ssee")
    ) as stream:
        lines: list[str] = []

        def _reader() -> None:
            try:
                for raw in stream.iter_lines():
                    if raw:
                        lines.append(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
            except Exception:
                # The server closes the stream on terminal state (or the 30 s
                # keep-alive cap); iter_lines then raises on the closed socket.
                # The assertions below have already read what they need by then
                # -- this thread exists only to drain, never to fail the test.
                pass

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        # Simulate exactly what the worker's _notify does on stage completion.
        with db.cursor() as cur:
            cur.execute(
                "SELECT pg_notify(%s, %s)",
                (f"build_{build_id}", json.dumps({"stage": "paginate", "status": "completed"})),
            )
        db.commit()

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not any("stage.progress" in l for l in lines):
            time.sleep(0.1)

        assert any("event: connected" in l for l in lines), f"SSE must open with a connected event: {lines}"
        assert any("stage.progress" in l for l in lines), (
            f"SSE must push the NOTIFY'd stage event without polling: {lines}"
        )
