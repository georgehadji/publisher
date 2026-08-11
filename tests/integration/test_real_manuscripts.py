"""
U2 -- build what the tenant actually submitted (docs/ARCHITECTURE_UPLIFT_PLAN.md §3, U2).

Gates (must FAIL against pre-U2 code):

1. PUT /v1/manuscripts/:id/upload streams the DOCX into CAS and records
   source_sha256. Pre-U2: the uploadUrl has no route behind it at all.
2. Two tenants, two different manuscripts -> the two builds produce DIFFERENT
   artifacts, and each artifact contains its own manuscript's text. Pre-U2:
   the worker ignores the build and renders the same fixture book for every
   tenant, so both builds' ast/1 artifacts are byte-identical.
3. A build whose document has no stored source fails with BAD_INPUT instead of
   silently building the fixture. Pre-U2: the fixture builds anyway.

On a host without weasyprint/Ghostscript the builds stop at the paginate engine
gate (status='failed', error_kind='engine_bug') -- AFTER the ingest -> extract
-> ast-assemble stages have run and recorded their artifacts. The artifact
divergence assertions below are the regression signal that works on every host;
with engines present the same mechanism yields two different press PDFs.
"""

from __future__ import annotations

import hashlib
import json

import psycopg2.extras
import pytest
import requests

from conftest import (
    RUN_ID,
    TEST_CAS_ROOT,
    _headers,
    api_server,
    create_uploaded_manuscript,
    insert_build,
    set_manuscript_source,
)

PROFILE_JSON = '["Generic 6x9"]'


def _artifact_sha(db, build_id: str, schema_id: str) -> str | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT sha256 FROM artifacts WHERE build_id = %s AND schema_id = %s",
            (build_id, schema_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _run_build(db, worker_module, build_id: str, document_id: str, tenant: str) -> dict:
    insert_build(db, build_id, document_id, tenant)
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM builds WHERE id = %s", (build_id,))
        build = dict(cur.fetchone())
    worker_module.run_build(db, build)
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status, error_kind FROM builds WHERE id = %s", (build_id,))
        return dict(cur.fetchone())


# ── Gate 1: the upload route ────────────────────────────────────


def test_upload_route_streams_docx_into_cas(api_server, make_docx):
    ms_id, docx = create_uploaded_manuscript(api_server, make_docx, "UPLOAD ME", "Body of the upload test.", tag="u2upload")
    sha = hashlib.sha256(docx).hexdigest()

    # The bytes must be readable back out of the shared CAS at the sharded path.
    blob = TEST_CAS_ROOT / sha[:2] / sha[2:4] / sha
    assert blob.is_file(), f"uploaded bytes not found in CAS at {blob}"
    assert blob.read_bytes() == docx, "CAS bytes differ from what was uploaded"

    # And the manuscript row must point at them.
    import psycopg2

    from conftest import DATABASE_URL

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_sha256, source_size FROM manuscripts WHERE id = %s", (ms_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == sha, f"manuscript row not updated: {row}"
    assert row[1] == len(docx)


def test_upload_route_rejects_non_docx(api_server, make_docx):
    ms_id, _ = create_uploaded_manuscript(api_server, make_docx, "REJECT ME", "Body.", tag="u2reject")
    resp = requests.put(
        f"{api_server}/v1/manuscripts/{ms_id}/upload",
        data=b"this is definitely not a zip archive",
        headers={
            **{k: v for k, v in _headers("u2g").items()},
            "Content-Type": "application/octet-stream",
        },
    )
    assert resp.status_code == 400, f"garbage upload must be rejected, got {resp.status_code}: {resp.text}"
    assert "not a DOCX" in resp.json().get("error", "")


def test_upload_route_rejects_oversized_docx(api_server, make_docx):
    """The upload size cap (PUBLISHER_UPLOAD_MAX_BYTES) is enforced with a 413."""
    ms_id, _ = create_uploaded_manuscript(api_server, make_docx, "BIG ME", "Body.", tag="u2big")
    big = b"PK\x03\x04" + b"\x00" * (100_100)  # over the 100 KB test cap, DOCX magic prefix
    resp = requests.put(
        f"{api_server}/v1/manuscripts/{ms_id}/upload",
        data=big,
        headers={
            **{k: v for k, v in _headers("u2big").items()},
            "Content-Type": "application/octet-stream",
        },
    )
    assert resp.status_code == 413, f"oversized upload must be rejected, got {resp.status_code}: {resp.text}"


# ── Gate 2: two manuscripts -> two different builds ─────────────


def test_two_manuscripts_produce_different_artifacts(db, worker_module, make_docx, register_tenant):
    ms_a = register_tenant("u2-tenant-a")
    ms_b = register_tenant("u2-tenant-b")
    set_manuscript_source(db, ms_a, make_docx("ALPHA NOVEL", "The wholly distinctive prose of tenant A."))
    set_manuscript_source(db, ms_b, make_docx("BETA NOVEL", "The completely different prose of tenant B."))

    status_a = _run_build(db, worker_module, f"test-{RUN_ID}-u2-a", ms_a, "test-tenant-u2-tenant-a")
    status_b = _run_build(db, worker_module, f"test-{RUN_ID}-u2-b", ms_b, "test-tenant-u2-tenant-b")

    # Both builds must reach a terminal state (completed with engines; failed at
    # the paginate gate without them -- never stuck, never the fixture).
    for label, st in (("A", status_a), ("B", status_b)):
        assert st["status"] in ("completed", "failed"), f"build {label} not terminal: {st}"

    sha_a = _artifact_sha(db, f"test-{RUN_ID}-u2-a", "ast/1")
    sha_b = _artifact_sha(db, f"test-{RUN_ID}-u2-b", "ast/1")
    assert sha_a and sha_b, "both builds must have produced an ast/1 artifact"

    assert sha_a != sha_b, (
        "both builds produced the SAME ast/1 artifact -- the worker is rendering "
        "the fixture manuscript instead of what each tenant uploaded (N1)"
    )

    # Each AST must actually contain its own manuscript's text.
    ast_a = json.loads((TEST_CAS_ROOT / sha_a[:2] / sha_a[2:4] / sha_a).read_text(encoding="utf-8"))
    ast_b = json.loads((TEST_CAS_ROOT / sha_b[:2] / sha_b[2:4] / sha_b).read_text(encoding="utf-8"))
    text_a = json.dumps(ast_a, ensure_ascii=False)
    text_b = json.dumps(ast_b, ensure_ascii=False)
    assert "tenant A" in text_a and "tenant A" not in text_b
    assert "tenant B" in text_b and "tenant B" not in text_a


# ── Gate 3: refuse to guess ─────────────────────────────────────


def test_build_without_uploaded_source_fails_bad_input(db, worker_module, register_tenant):
    ms_id = register_tenant("u2-nosource")
    status = _run_build(db, worker_module, f"test-{RUN_ID}-u2-nosource", ms_id, "test-tenant-u2-nosource")

    assert status["status"] == "failed", (
        f"a build with no stored source must fail, got {status['status']}"
    )
    assert status["error_kind"] == "bad_input", (
        f"must be BAD_INPUT, got {status['error_kind']}: {status}"
    )


def test_unknown_profile_is_bad_input(db, worker_module, make_docx, register_tenant):
    ms_id = register_tenant("u2-badprofile")
    set_manuscript_source(db, ms_id, make_docx("PROFILE TEST", "Body for the profile test."))

    build_id = f"test-{RUN_ID}-u2-badprofile"
    insert_build(db, build_id, ms_id, "test-tenant-u2-badprofile",
                 profile_ids='["No Such Profile"]')
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM builds WHERE id = %s", (build_id,))
        build = dict(cur.fetchone())
    worker_module.run_build(db, build)
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status, error_kind FROM builds WHERE id = %s", (build_id,))
        status = dict(cur.fetchone())

    assert status["status"] == "failed" and status["error_kind"] == "bad_input", (
        f"unknown profile must be refused, got {status}"
    )
