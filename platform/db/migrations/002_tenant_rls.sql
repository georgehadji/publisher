-- E4.2 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- tenant_id everywhere + Row-
-- Level Security. Closes L13; answers T2.
--
-- Before this: `artifacts` and `build_stages` carried no tenant_id, and
-- nothing in the database enforced isolation at all -- it depended entirely
-- on every route remembering to call `loadOwned()` first. Today every route
-- does; nothing made that true tomorrow. This makes a cross-tenant row
-- UNREPRESENTABLE (the composite foreign key below) and UNREADABLE (the RLS
-- policy), not merely unqueried.

-- The referenced side of the composite FKs below: a (build_id, tenant_id)
-- pair is only meaningful if a build with that exact id+tenant combination
-- exists.
ALTER TABLE builds ADD CONSTRAINT builds_id_tenant_uniq UNIQUE (id, tenant_id);

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE artifacts a SET tenant_id = b.tenant_id
    FROM builds b WHERE b.id = a.build_id AND a.tenant_id IS NULL;
ALTER TABLE artifacts ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_tenant_fk
    FOREIGN KEY (build_id, tenant_id) REFERENCES builds (id, tenant_id);

ALTER TABLE build_stages ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE build_stages bs SET tenant_id = b.tenant_id
    FROM builds b WHERE b.id = bs.build_id AND bs.tenant_id IS NULL;
ALTER TABLE build_stages ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE build_stages ADD CONSTRAINT build_stages_tenant_fk
    FOREIGN KEY (build_id, tenant_id) REFERENCES builds (id, tenant_id);

-- Row-Level Security across every tenant-scoped table -- not just artifacts
-- and build_stages: titles/manuscripts/webhooks/idempotency_keys already
-- carry tenant_id and are exactly as exposed to a forgotten WHERE clause.
-- `current_setting('app.tenant_id', true)` (the `true` = "missing_ok") returns
-- NULL rather than raising when unset, and `tenant_id = NULL` is never true --
-- a connection that never sets the GUC sees zero rows, not every tenant's.
--
-- `cache_index` is deliberately excluded: it has no tenant_id (a cache key
-- is content-derived and legitimately shared across tenants).
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'titles', 'manuscripts', 'builds', 'artifacts', 'build_stages',
        'webhooks', 'idempotency_keys'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        -- No explicit FOR/WITH CHECK: Postgres uses this USING expression as
        -- the WITH CHECK for INSERT/UPDATE too when none is given, so a row
        -- written with a tenant_id other than the current app.tenant_id is
        -- rejected, not merely hidden on read.
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true))',
            t
        );
    END LOOP;
END $$;
