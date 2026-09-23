-- The worker reads a manuscript's override log (004) to hand it to `resolve` as
-- the build's `overrides_path` root input.
--
-- SELECT only: the worker applies reviewer decisions, it never makes one. The
-- read runs on the worker's tenant-scoped connection (app.tenant_id set to the
-- claimed build's tenant in worker.py's poll loop, just before run_build), so
-- override_ops' RLS policy scopes it the same way it scopes the API.
GRANT SELECT ON override_ops TO publisher_worker;
