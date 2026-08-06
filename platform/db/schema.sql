-- Publisher durable state -- ARCHITECTURE_REMEDIATION.md A1.2, A1.3.
--
-- Single source of truth for both tables the worker and API share across the
-- process boundary that didn't exist before this plan (findings 1, 2, 3).
-- Applied automatically by postgres's docker-entrypoint-initdb.d on first
-- container start (see docker-compose.yml); re-run manually otherwise:
--   psql "$DATABASE_URL" -f platform/db/schema.sql

-- output_refs: artifact_kind -> {sha256, media_type, size}. A hash alone
-- can't rehydrate a StageResult on a cache hit -- ArtifactRef requires
-- media_type and size too, so the cached record carries what it takes to
-- reconstruct one without re-deriving it from the blob.
CREATE TABLE IF NOT EXISTS cache_index (
    cache_key    TEXT PRIMARY KEY,
    stage        TEXT NOT NULL,
    version      INTEGER NOT NULL,
    output_refs  JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    hit_count    INTEGER NOT NULL DEFAULT 0,
    last_hit_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS builds (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    document_id   TEXT NOT NULL,
    design_id     TEXT,
    profile_ids   JSONB,
    mode          TEXT NOT NULL DEFAULT 'proof',
    status        TEXT NOT NULL DEFAULT 'queued',  -- queued|running|completed|failed
    error_kind    TEXT,
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS builds_tenant_idx ON builds (tenant_id);
-- Admission and the worker's claim query both filter status='queued' ordered
-- by age; a plain scan over every build ever created doesn't scale past a
-- handful of rows.
CREATE INDEX IF NOT EXISTS builds_queued_idx ON builds (created_at) WHERE status = 'queued';

CREATE TABLE IF NOT EXISTS build_stages (
    build_id      TEXT NOT NULL REFERENCES builds(id),
    stage_name    TEXT NOT NULL,
    stage_version INTEGER NOT NULL,
    status        TEXT NOT NULL,  -- running|completed|failed
    cache_hit     BOOLEAN NOT NULL DEFAULT FALSE,
    duration_ms   INTEGER,
    metrics       JSONB,
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    PRIMARY KEY (build_id, stage_name)
);

-- A2.1 -- the API's in-memory Map()s (titles, manuscripts, idempotency
-- replay, webhooks). Same shape as the TS interfaces in packages/api/src/
-- index.ts; tenant_id is load-bearing on every one so the existing
-- assertTenant() checks survive the move off in-memory state unchanged.

CREATE TABLE IF NOT EXISTS titles (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    title      TEXT NOT NULL,
    author     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS titles_tenant_idx ON titles (tenant_id);

CREATE TABLE IF NOT EXISTS manuscripts (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    title_id   TEXT NOT NULL REFERENCES titles(id),
    status     TEXT NOT NULL DEFAULT 'uploaded',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS manuscripts_tenant_idx ON manuscripts (tenant_id);

-- Idempotency-Key replay: a retried POST/PATCH with the same key (scoped to
-- tenant, so two tenants cannot collide on or read each other's keys)
-- returns the first response instead of creating a second resource.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    tenant_id    TEXT NOT NULL,
    idem_key     TEXT NOT NULL,
    status_code  INTEGER NOT NULL,
    body         JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, idem_key)
);

CREATE TABLE IF NOT EXISTS webhooks (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    url        TEXT NOT NULL,
    events     JSONB NOT NULL,
    secret     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS webhooks_tenant_idx ON webhooks (tenant_id);

-- An artifact is identified by its SCHEMA ID, not by `kind`.
--
-- `kind` is stage-LOCAL: it is the key inside one stage's `outputs={}` dict, so
-- it is only unique within that stage. Two stages legitimately both emit
-- kind='pdf' (paginate -> raw-pdf/1, finish -> pdfx/1) and three emit
-- kind='report'. With PRIMARY KEY (build_id, kind) plus the worker's
-- ON CONFLICT DO NOTHING, the FIRST stage to finish won and every later
-- artifact of the same kind was silently dropped -- so `pdf` resolved to
-- paginate's unconverted weasyprint output and the API served it as the
-- press-ready file, while the real PDF/X-1a from `finish` was discarded.
-- Preflight's report lost to finish's report the same way.
--
-- schema_id is the identity the DAG itself already runs on (one stage's output
-- schema matched to another's input schema), and it is globally unique.
CREATE TABLE IF NOT EXISTS artifacts (
    build_id   TEXT NOT NULL REFERENCES builds(id),
    kind       TEXT NOT NULL,
    schema_id  TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size       BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (build_id, schema_id)
);
CREATE INDEX IF NOT EXISTS artifacts_kind_idx ON artifacts (build_id, kind);
