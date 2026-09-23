"""
The override log -- PATCH /v1/documents/:id/overrides persists what it accepts.

The route used to validate, check tenancy, return `{applied: ops.length}` and
store nothing. These run against the real API and a real Postgres, because
every property that matters here is the database's: that the rows exist, in
the order sent, that a batch lands whole or not at all, that an op cannot be
rewritten, and that another tenant cannot see it. Validation and the refusal
of unapplicable ops are covered in-process by packages/api's overrides.test.ts.

Must fail against the pre-004 code: there, every PATCH answered 200 and the
GET's `overrides` was a literal [].
"""

from __future__ import annotations

import psycopg2
import pytest
import requests

from conftest import DATABASE_URL, RUN_ID, TENANT, TOKEN, api_server, create_uploaded_manuscript, make_docx  # noqa: F401

# The unprivileged role the API runs as in production (003_least_privilege_roles.sql,
# which also sets this password).
APP_DSN = DATABASE_URL.replace("publisher:publisher@", "publisher_app:publisher_app@")


def _op(op_id: str, **fields) -> dict:
    return {
        "id": op_id,
        "sourceRef": {"docxId": "p1"},
        "op": "retitle",
        "value": "Retitled",
        "actor": "user:reviewer",
        "at": "2026-01-01T00:00:00Z",
        **fields,
    }


def _patch(base: str, manuscript_id: str, ops: list[dict], idem: str) -> requests.Response:
    return requests.patch(
        f"{base}/v1/documents/{manuscript_id}/overrides",
        json={"ops": ops},
        headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": f"{RUN_ID}-{idem}"},
    )


def _logged(manuscript_id: str) -> list[str]:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM override_ops WHERE manuscript_id = %s ORDER BY seq",
                (manuscript_id,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


@pytest.fixture(scope="module")
def manuscript(api_server, make_docx) -> str:
    return create_uploaded_manuscript(api_server, make_docx, "OVERRIDES", "Body.", tag="ovlog")[0]


def test_ops_are_stored_in_the_order_sent_and_read_back(api_server, manuscript):
    flag = _op("ov-a-flag", op="flag_ambiguity", rationale="two chapters or one?")
    del flag["value"]
    ops = [_op("ov-b-retitle"), flag]
    res = _patch(api_server, manuscript, ops, "order")
    assert res.status_code == 201, res.text
    assert res.json()["appended"] == ["ov-b-retitle", "ov-a-flag"]

    # Body order, not id order: apply_overrides is order-sensitive.
    assert _logged(manuscript) == ["ov-b-retitle", "ov-a-flag"]

    structure = requests.get(
        f"{api_server}/v1/manuscripts/{manuscript}/structure",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ).json()
    assert structure["overrides"] == ops, "the log is returned verbatim, in order"
    # No build has run, so there is no AST to check the ops against: unknown,
    # not "none orphaned".
    assert structure["status"] == "pending" and structure["orphanedOps"] is None


def test_a_logged_op_cannot_be_resent(api_server, manuscript):
    """Ops are immutable once logged. A second PATCH reusing an id is a 409,
    not a silent overwrite -- and the log is unchanged by the attempt."""
    before = _logged(manuscript)
    assert "ov-b-retitle" in before, "depends on the ordering test's op"
    res = _patch(api_server, manuscript, [_op("ov-b-retitle", value="Different")], "resend")
    assert res.status_code == 409, res.text
    assert _logged(manuscript) == before


def test_a_batch_lands_whole_or_not_at_all(api_server, manuscript):
    """One duplicate in a batch rejects the batch: the new op beside it must
    not be half-committed."""
    before = _logged(manuscript)
    res = _patch(api_server, manuscript, [_op("ov-new-in-batch"), _op("ov-b-retitle")], "atomic")
    assert res.status_code == 409, res.text
    assert _logged(manuscript) == before
    assert "ov-new-in-batch" not in _logged(manuscript)


def test_an_op_the_build_cannot_apply_is_never_stored(api_server, manuscript):
    """The log is append-only, so a `split` stored now would fail every later
    build of this manuscript (resolve raises UnsupportedOverrideOp)."""
    before = _logged(manuscript)
    res = _patch(api_server, manuscript, [_op("ov-split", op="split")], "split")
    assert res.status_code == 422, res.text
    assert _logged(manuscript) == before


def test_the_app_role_can_append_but_never_rewrite(manuscript):
    """Append-only by GRANT, not by convention: publisher_app -- the role the
    API runs as in production -- holds SELECT and INSERT on override_ops only."""
    for statement in (
        "UPDATE override_ops SET op = '{}'::jsonb",
        "DELETE FROM override_ops",
    ):
        conn = psycopg2.connect(APP_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute(statement)
        finally:
            conn.rollback()
            conn.close()


def _visible_to(tenant: str) -> int:
    """Rows of override_ops publisher_app sees under `tenant`'s GUC, with no
    WHERE clause at all -- only RLS decides."""
    conn = psycopg2.connect(APP_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
            cur.execute("SELECT count(*) FROM override_ops")
            (count,) = cur.fetchone()
        return count
    finally:
        conn.rollback()
        conn.close()


def test_another_tenant_sees_none_of_the_log(manuscript):
    """RLS scopes the log, not the route. Checked both ways: zero rows for a
    stranger means nothing unless the owner, same query, sees theirs."""
    assert _visible_to(TENANT) >= 2, "the owner must see its own ops, or the zero below is vacuous"
    assert _visible_to("some-other-tenant") == 0
