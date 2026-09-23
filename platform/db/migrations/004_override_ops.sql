-- Override ops -- the human-decision log beside the derived AST (ARCHITECTURE.md
-- §2.6, schemas/overrides/overrides.schema.json).
--
-- PATCH /v1/documents/:id/overrides validated its body, checked tenancy, then
-- returned `{applied: ops.length}` and stored nothing: every reviewer decision
-- was acknowledged and discarded. This is where they now go.
--
-- Append-only by grant, not by convention: publisher_app gets SELECT and INSERT
-- and nothing else, so no route can rewrite or drop a decision a reviewer made
-- (overrides.py: "Overrides are immutable"). `seq` fixes application order,
-- which `created_at` cannot -- two ops in one PATCH share a timestamp, and
-- apply_overrides is order-sensitive (a retitle then a delete is not a delete
-- then a retitle).

-- The referenced side of the composite FK below, as 002 did for builds: a
-- (manuscript_id, tenant_id) pair is only meaningful if that exact pair exists,
-- so an op filed under another tenant's manuscript is unrepresentable.
ALTER TABLE manuscripts ADD CONSTRAINT manuscripts_id_tenant_uniq UNIQUE (id, tenant_id);

CREATE TABLE IF NOT EXISTS override_ops (
    seq           BIGINT GENERATED ALWAYS AS IDENTITY,
    manuscript_id TEXT NOT NULL,
    tenant_id     TEXT NOT NULL,
    id            TEXT NOT NULL,          -- the op's own `ov-...` id
    op            JSONB NOT NULL,         -- one overrides/1 `overrideOp`, as sent
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (manuscript_id, id),
    CONSTRAINT override_ops_tenant_fk
        FOREIGN KEY (manuscript_id, tenant_id) REFERENCES manuscripts (id, tenant_id)
);
CREATE INDEX IF NOT EXISTS override_ops_order_idx ON override_ops (manuscript_id, seq);

ALTER TABLE override_ops ENABLE ROW LEVEL SECURITY;
ALTER TABLE override_ops FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON override_ops;
CREATE POLICY tenant_isolation ON override_ops
    USING (tenant_id = current_setting('app.tenant_id', true));

-- No UPDATE, no DELETE: see the header. An identity column needs no separate
-- sequence grant for INSERT to use it.
GRANT SELECT, INSERT ON override_ops TO publisher_app;
