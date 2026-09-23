"""
E4 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- migrations, tenant_id + Row-Level
Security, and least-privilege roles.

Gates (must FAIL against pre-E4 code):

1. E4.1 -- an EXISTING deployment (one that already has the 001_initial.sql
   shape, applied the old docker-entrypoint-initdb.d way) has a path to the
   002/003 schema change: applying pending migrations in order upgrades it,
   and re-applying is a no-op. Pre-E4.1: schema.sql only ever ran against an
   empty data directory; nothing else could apply 002/003 to a live database.
2. E4.2 -- `SELECT * FROM artifacts` with NO WHERE clause, connected as the
   unprivileged `publisher_app` role with `app.tenant_id` set, returns ONLY
   that tenant's rows. Pre-E4.2: artifacts/build_stages had no tenant_id and
   no RLS at all -- isolation depended entirely on every route remembering
   loadOwned() first.
3. E4.3 -- `publisher_app`/`publisher_worker` are neither the table owner nor
   BYPASSRLS, so migration 002's policy actually constrains them (an owner or
   a BYPASSRLS role bypasses RLS regardless of the policy). `publisher`, the
   owner/superuser role these integration tests' OWN fixtures connect as,
   keeps working unchanged -- superusers always bypass row security
   regardless of FORCE ROW LEVEL SECURITY (Postgres's own documented
   behaviour), which is exactly why `insert_build`/`_register_tenant`/etc.
   elsewhere in this suite needed no changes for this plan to land.

Runs its own scratch database on the SAME Postgres server the rest of the
integration suite uses (never the shared `publisher` database), so it can
freely apply/re-apply migrations and drop the database afterward without
disturbing any other test.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

from conftest import DATABASE_URL

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "platform" / "db" / "migrations"

TENANT_TABLES = [
    "titles", "manuscripts", "builds", "artifacts", "build_stages",
    "webhooks", "idempotency_keys", "override_ops",
]

# Every migration on disk, by its numeric prefix -- what migrate.ts applies.
# Derived rather than hand-listed: a literal [1, 2, 3] had to be edited by
# every new migration, and still passed if a file on disk was never applied
# so long as the literal was stale too.
EXPECTED_VERSIONS = sorted(int(p.name.split("_")[0]) for p in MIGRATIONS_DIR.glob("*.sql"))


def _admin_dsn(dbname: str = "publisher") -> str:
    """Same server/credentials as DATABASE_URL, pointed at a different database."""
    base = DATABASE_URL.rsplit("/", 1)[0]
    return f"{base}/{dbname}"


def _apply_pending_migrations(dsn: str) -> None:
    """The same ledger-and-apply logic as packages/api/src/migrate.ts and
    ci.yml's "Load schema" step, reimplemented here in Python so this test
    needs no Node toolchain -- what matters for E4.1's acceptance is that the
    SQL files themselves are safely re-appliable, not which language runs
    them."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    INTEGER PRIMARY KEY,
                    name       TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(path.name.split("_")[0])
            if version in applied:
                continue
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                    (version, path.name),
                )
            conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def scratch_db():
    """A fresh, empty database on the same Postgres server -- isolated from
    the shared `publisher` database every other integration test writes to,
    so this file can migrate/re-migrate/drop freely."""
    name = f"test_migrate_scratch_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(_admin_dsn())
    admin.autocommit = True  # CREATE/DROP DATABASE cannot run inside a transaction
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        yield _admin_dsn(name)
    finally:
        with admin.cursor() as cur:
            # Terminate any lingering connections (e.g. a test's own psycopg2
            # connection it forgot to close) -- DROP DATABASE refuses while any
            # connection to it is open.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _table_shape(dsn: str) -> dict[str, dict]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = ANY(%s)",
                (TENANT_TABLES,),
            )
            rls = {row["relname"]: dict(row) for row in cur.fetchall()}
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'artifacts' AND column_name = 'tenant_id'"
            )
            has_tenant_col = cur.fetchone() is not None
            cur.execute(
                "SELECT to_regclass('schema_migrations') IS NOT NULL AS exists"
            )
            if cur.fetchone()["exists"]:
                cur.execute("SELECT version FROM schema_migrations ORDER BY version")
                versions = [row["version"] for row in cur.fetchall()]
            else:
                versions = []
        return {"rls": rls, "artifacts_has_tenant_id": has_tenant_col, "versions": versions}
    finally:
        conn.close()


def test_migrate_upgrades_an_existing_deployment_and_is_idempotent(scratch_db):
    # Simulate an "old" deployment: only 001 has ever been applied (the
    # docker-entrypoint-initdb.d era, before 002/003 existed).
    conn = psycopg2.connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute((MIGRATIONS_DIR / "001_initial.sql").read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()

    before = _table_shape(scratch_db)
    assert not before["artifacts_has_tenant_id"], "scratch DB already has 002 applied -- test setup is wrong"

    _apply_pending_migrations(scratch_db)

    after = _table_shape(scratch_db)
    assert after["artifacts_has_tenant_id"], "migrate did not add artifacts.tenant_id (002)"
    for table in TENANT_TABLES:
        assert after["rls"][table]["relrowsecurity"], f"{table} does not have RLS enabled"
        assert after["rls"][table]["relforcerowsecurity"], f"{table} does not FORCE RLS"
    assert after["versions"] == EXPECTED_VERSIONS, f"expected {EXPECTED_VERSIONS} applied, got {after['versions']}"

    # Re-running must be a no-op: same ledger, no error.
    _apply_pending_migrations(scratch_db)
    idempotent = _table_shape(scratch_db)
    assert idempotent["versions"] == EXPECTED_VERSIONS


def test_rls_isolates_tenants_with_no_where_clause(scratch_db):
    _apply_pending_migrations(scratch_db)

    # Seed two tenants' data as the owner/superuser role (DATABASE_URL's own
    # role, `publisher` in docker-compose.yml -- confirmed a real Postgres
    # superuser, which is why this bypasses RLS to set up cross-tenant
    # fixtures the same way tests/integration/conftest.py's helpers do).
    seed = psycopg2.connect(scratch_db)
    try:
        with seed.cursor() as cur:
            cur.execute("INSERT INTO titles (id, tenant_id, title) VALUES ('t-a', 'tenant-a', 'A'), ('t-b', 'tenant-b', 'B')")
            cur.execute("INSERT INTO manuscripts (id, tenant_id, title_id) VALUES ('ms-a', 'tenant-a', 't-a'), ('ms-b', 'tenant-b', 't-b')")
            cur.execute(
                "INSERT INTO builds (id, tenant_id, document_id, mode, status) VALUES "
                "('build-a', 'tenant-a', 'ms-a', 'proof', 'completed'), "
                "('build-b', 'tenant-b', 'ms-b', 'proof', 'completed')"
            )
            cur.execute(
                "INSERT INTO artifacts (build_id, tenant_id, kind, schema_id, sha256, media_type, size) VALUES "
                "('build-a', 'tenant-a', 'pdf', 'pdfx/1', %s, 'application/pdf', 100), "
                "('build-b', 'tenant-b', 'pdf', 'pdfx/1', %s, 'application/pdf', 100)",
                ("a" * 64, "b" * 64),
            )
        seed.commit()
    finally:
        seed.close()

    # The actual E4.2 acceptance: connect as the unprivileged app role, set
    # ONLY the tenant GUC, and prove a bare SELECT with NO WHERE clause is
    # already scoped -- this is the database doing the work, not the query.
    dsn = scratch_db.replace("publisher:publisher@", "publisher_app:x@")
    app_conn = psycopg2.connect(dsn)
    try:
        with app_conn.cursor() as cur:
            cur.execute("SET app.tenant_id = 'tenant-a'")
            cur.execute("SELECT build_id, tenant_id FROM artifacts")
            rows = cur.fetchall()
        assert rows == [("build-a", "tenant-a")], (
            f"publisher_app with app.tenant_id='tenant-a' saw {rows} -- "
            "expected only tenant-a's row, with no WHERE clause in the query"
        )

        # No GUC set at all -- fail closed to zero rows, not every tenant's.
        with app_conn.cursor() as cur:
            cur.execute("RESET app.tenant_id")
            cur.execute("SELECT count(*) FROM artifacts")
            (count,) = cur.fetchone()
        assert count == 0, f"expected 0 rows with app.tenant_id unset, got {count}"
    finally:
        app_conn.close()


def test_publisher_app_and_worker_are_not_owner_and_lack_bypassrls(scratch_db):
    """E4.3's acceptance, the role-attribute half: RLS (E4.2) does nothing if
    the connecting role owns the tables or has BYPASSRLS."""
    _apply_pending_migrations(scratch_db)
    conn = psycopg2.connect(scratch_db)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('publisher_app', 'publisher_worker')"
            )
            roles = {row["rolname"]: row for row in cur.fetchall()}
            cur.execute("SELECT tableowner FROM pg_tables WHERE tablename = 'artifacts'")
            (owner,) = cur.fetchone()
    finally:
        conn.close()

    for name in ("publisher_app", "publisher_worker"):
        assert not roles[name]["rolsuper"], f"{name} must not be a superuser"
        assert not roles[name]["rolbypassrls"], f"{name} must not have BYPASSRLS"
        assert name != owner, f"{name} must not own the tenant tables"
