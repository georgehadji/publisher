-- A stage's diagnostics reach the reviewer (docs/WIRING_PLAN.md W0).
--
-- StageResult.warnings was read by nothing: not the executor, the worker or
-- the API. A non-fatal report (an override op that does not fit the document,
-- a caliper the spine used by default) was computed and dropped, so the build
-- looked clean. `build_stages.diagnostics` holds each stage's warnings, or a
-- failed stage's diagnostics; GET /v1/builds/:id returns them.
--
-- `cache_index.diagnostics` keeps a stage's metrics and warnings with its
-- artifacts. A cache hit returned artifacts alone, so a warning vanished from
-- every cached rebuild of the same inputs.
--
-- Both grants are table-level (003), so the new columns need none.
ALTER TABLE build_stages ADD COLUMN IF NOT EXISTS diagnostics JSONB;
ALTER TABLE cache_index ADD COLUMN IF NOT EXISTS diagnostics JSONB;
