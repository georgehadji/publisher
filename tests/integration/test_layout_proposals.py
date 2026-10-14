"""
The Compositor's fixes reach the override log only through a reviewer's accept
(K4, docs/COMPOSITOR_PATCH_PLAN.md). The structure view lists a build's
layout-review/1 proposals beside structure-propose's, and the accept route turns
one into the set_attr op the log requires -- path included, which a proposal does
not carry. Against the real API and Postgres, because the op that lands is the
database's to show. The op's shape is covered in-process by manuscripts.test.ts.
"""

from __future__ import annotations

import json

import psycopg2
import requests

from conftest import (DATABASE_URL, RUN_ID, TENANT, TOKEN, api_server, create_uploaded_manuscript,  # noqa: F401
                      insert_build, make_docx, write_to_cas)

PID = "pr-" + "a" * 24
AST = {"schema": "ast/1", "body": [{"type": "chapter", "attrs": {"number": 1, "title": "One"},
                                    "sourceRef": {"docxId": "c1"}, "content": [
    {"type": "paragraph", "sourceRef": {"docxId": "p7"}, "content": [{"type": "text", "text": "Words."}]}]}]}
REVIEW = {"schema": "layout-review/1", "agentId": "compositor", "findings": [
    {"kind": "widow", "page": 5, "verdict": "confirmed",
     "proposals": [{"id": PID, "type": "fit_paragraph", "docxId": "p7", "value": "tight"}]}],
    "fitOutcomes": [{"docxId": "p6", "fit": "tight", "outcome": "cleared"}]}


def _built(db, cas_root, manuscript_id: str) -> None:
    """A build of the manuscript that produced an AST and a layout review."""
    build_id = f"test-k4-{RUN_ID}"
    insert_build(db, build_id, manuscript_id, TENANT)
    with db.cursor() as cur:
        for kind, schema_id, doc in (("ast", "ast/1", AST), ("review", "layout-review/1", REVIEW)):
            data = json.dumps(doc).encode("utf-8")
            cur.execute("INSERT INTO artifacts (build_id, tenant_id, kind, schema_id, sha256, media_type, size) "
                        "VALUES (%s, %s, %s, %s, %s, 'application/json', %s)",
                        (build_id, TENANT, kind, schema_id, write_to_cas(cas_root, data), len(data)))
    db.commit()


def _logged_op(manuscript_id: str, op_id: str) -> dict | None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT op FROM override_ops WHERE manuscript_id = %s AND id = %s", (manuscript_id, op_id))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def test_a_layout_proposal_is_listed_and_accepting_it_logs_set_attr_on_fit(api_server, make_docx, db, cas_root):
    manuscript_id = create_uploaded_manuscript(api_server, make_docx, "K4", "Body.", tag="k4")[0]
    _built(db, cas_root, manuscript_id)
    auth = {"Authorization": f"Bearer {TOKEN}"}

    view = requests.get(f"{api_server}/v1/manuscripts/{manuscript_id}/structure", headers=auth).json()
    [listed] = [p for p in view["proposals"] if p["id"] == PID]
    assert (listed["op"], listed["docxId"], listed["page"], listed["kind"]) == ("set_attr", "p7", 5, "widow")
    # K5: the same build's fit outcomes, off the same artifact
    assert view["fitOutcomes"] == [{"docxId": "p6", "fit": "tight", "outcome": "cleared"}]

    accept = f"{api_server}/v1/manuscripts/{manuscript_id}/proposals/{PID}/accept"
    first = requests.post(accept, json={"actor": "user:reviewer"},
                          headers={**auth, "Idempotency-Key": f"{RUN_ID}-k4-1"})
    assert first.status_code == 201, first.text
    op = _logged_op(manuscript_id, f"ov-{PID}")
    assert (op["op"], op["path"], op["value"], op["sourceRef"]["docxId"]) == ("set_attr", "/attrs/fit", "tight", "p7")

    again = requests.post(accept, json={"actor": "user:reviewer"},
                          headers={**auth, "Idempotency-Key": f"{RUN_ID}-k4-2"})
    assert again.status_code == 409
    view = requests.get(f"{api_server}/v1/manuscripts/{manuscript_id}/structure", headers=auth).json()
    assert PID not in [p["id"] for p in view["proposals"]]
