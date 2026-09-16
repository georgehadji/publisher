-- E4.3 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- least-privilege roles. Answers
-- T2 (second half): RLS (002) does nothing if the connecting role owns the
-- tables (an owner bypasses RLS by default) or carries BYPASSRLS. The
-- migration/bootstrap role (POSTGRES_USER, `publisher` in docker-compose.yml)
-- stays the table owner and a Postgres superuser (the official postgres
-- image grants its POSTGRES_USER superuser at cluster init) -- superusers
-- always bypass row security regardless of FORCE, which is exactly the
-- "trusted migration and test-fixture-setup account" role this repo's own
-- integration-test helpers (tests/integration/conftest.py) already depend
-- on: they seed arbitrary cross-tenant fixture rows directly and must keep
-- working unchanged.
--
-- Two new, unprivileged roles do the actual application work:
--   publisher_app    -- the API (packages/api). Not owner, no BYPASSRLS.
--   publisher_worker -- the worker (worker.py). Not owner, no BYPASSRLS in
--                       its own session -- see publisher_worker_claim below.
--
-- Passwords are placeholders: docker-compose.yml's postgres service runs
-- with POSTGRES_HOST_AUTH_METHOD=trust (local dev/test only, see that
-- file's own comment), so the network layer does not check them. A real
-- deployment replaces trust auth and rotates these via a secrets manager,
-- not this file.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'publisher_app') THEN
        CREATE ROLE publisher_app LOGIN PASSWORD 'publisher_app';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'publisher_worker') THEN
        CREATE ROLE publisher_worker LOGIN PASSWORD 'publisher_worker';
    END IF;
    -- The worker's claim query (`SELECT ... FOR UPDATE SKIP LOCKED` in
    -- worker.py's _claim_build) is cross-tenant BY DESIGN -- it does not yet
    -- know which tenant's build it will find. This is the ONLY role with
    -- BYPASSRLS, and worker.py SET ROLEs into it for JUST that claim
    -- statement, then back to publisher_worker (which sets app.tenant_id to
    -- the claimed build's tenant and runs everything else, including the
    -- lease-holding UPDATE that ends the claim transaction, under the same
    -- policy as every other connection) -- see worker.py's _claim_build.
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'publisher_worker_claim') THEN
        CREATE ROLE publisher_worker_claim LOGIN PASSWORD 'publisher_worker_claim' BYPASSRLS;
    END IF;
    -- /v1/admin/metrics (admin.ts) reports GLOBAL aggregates across every
    -- tenant's build_stages/builds by design -- it is gated by its own
    -- separate admin-token check (plugins.ts's isAdminToken), not a tenant
    -- token, so there is no per-tenant app.tenant_id to set in the first
    -- place. A dedicated bypass role, used ONLY by that one route (db.ts's
    -- `adminPool`), keeps the blast radius of "sees every tenant's data" to
    -- exactly the query that is supposed to.
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'publisher_admin') THEN
        CREATE ROLE publisher_admin LOGIN PASSWORD 'publisher_admin' BYPASSRLS;
    END IF;
END $$;

-- Lets publisher_worker's session issue `SET ROLE publisher_worker_claim`.
GRANT publisher_worker_claim TO publisher_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    titles, manuscripts, builds, artifacts, build_stages, webhooks, idempotency_keys
    TO publisher_app;

-- The worker never creates titles/webhooks and only reads manuscripts (the
-- uploaded source it renders); it owns builds/build_stages/artifacts and the
-- untenanted cache.
GRANT SELECT ON manuscripts TO publisher_worker;
GRANT SELECT, INSERT, UPDATE ON builds, build_stages, artifacts TO publisher_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON cache_index TO publisher_worker;

-- Only what the claim statement itself touches: find a candidate row and
-- flip it to 'running' (or dead-letter it) before handing control back to
-- the tenant-scoped publisher_worker role.
GRANT SELECT, UPDATE ON builds TO publisher_worker_claim;

GRANT SELECT ON builds, build_stages TO publisher_admin;

-- FORCE ROW LEVEL SECURITY: without it, RLS still applies to publisher_app
-- and publisher_worker (both non-owners) exactly as migration 002 wrote it --
-- FORCE only changes what happens if a NON-BYPASSRLS role that happens to
-- OWN the table connects (none does today). Set anyway, defensively, so a
-- future role granted ownership without also being audited for BYPASSRLS
-- does not silently regain full visibility.
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'titles', 'manuscripts', 'builds', 'artifacts', 'build_stages',
        'webhooks', 'idempotency_keys'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;
