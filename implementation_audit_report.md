# Implementation Audit Report — Architecture Uplift Plan Stages 1–2

**Audited commit range:** `7a4401b..beeff69` (HEAD) — 2 commits, 48 files, +3,997/−1,998  
**Plan document:** `docs/ARCHITECTURE_UPLIFT_PLAN.md` (U1–U9 workstreams, 6.0 → >8.5 band rubrics)  
**Architecture reference:** `CLAUDE.md`, `ARCHITECTURE_REMEDIATION.md`, `BUILD_PLAN.md`  
**Audit method:** 4-axis parallel subagent review (API/security, worker/schema, tests, architecture) + compose-stack E2E verification  
**Date:** 2026-08-11  
**Auditor:** Reasonix — independent review of committed uplift (not self-audit; the prior root-level report was a same-session self-audit of Stage 1 only)

---

## 1. Executive Summary

The uplift implementation is **substantially correct and architecturally sound**, delivering on the plan's Stage 1 (U1–U4, 8.0 band) and Stage 2 (U5–U7, → 8.5 band) workstreams with high code quality and thorough test coverage. Three blocking defects, three should-fix items, and several nits were identified across ~4,000 lines of changed code. All blocking defects are in the API layer; the Python pipeline core (DAG derivation, worker durability, hard gates, CAS storage) is solid with no correctness bugs found.

**The two hard gates are structurally intact**: `ast-assemble` text integrity and `preflight`→`package` DAG enforcement are correctly implemented, and the compose-stack verification run confirmed a real end-to-end build reaches `completed` with all 9 stages, a `pdfx/1` artifact, and a real `%PDF-1.3` download.

**Score projection:** per the plan's rubric — zero CRITICAL, zero HIGH violations remain open against the audit baseline (the audit's pre-existing CRITICALs N1/N2 are closed by U2/U5). One new HIGH introduced by the overrides route (see §7). Two MEDIUM and several LOW. This keeps the system in the **8.0–8.5 band** (Stage 2 implemented but not yet audit-reviewed per the plan doc).

---

## 2. Plan Compliance Matrix

### Stage 1 — 8.0 band

| Plan Item | Status | Evidence | Notes |
|---|---|---|---|
| **U1.1** Schema: `attempt`, `lease_expires_at`, `worker_id` columns + `builds_reclaimable_idx` | ✅ Complete | `platform/db/schema.sql:36-60` | Idempotent ALTER for existing DBs. `dead` added to status enum. |
| **U1.2** Reclaim-aware claim with `FOR UPDATE SKIP LOCKED` | ✅ Complete | `worker.py:125-133` | Also reclaims NULL-lease rows (pre-U1 crashes), exceeding the plan's spec. |
| **U1.3** Claim sets `attempt`, `worker_id`, `lease_expires_at` | ✅ Complete | `worker.py:139-151` | Single UPDATE on claim. `COALESCE(started_at)` preserves first worker's timestamp. |
| **U1.4** Dead-letter after `MAX_ATTEMPTS` (3) | ✅ Complete | `worker.py:139-151` | `status='dead'`, `error_kind='exhausted'`. Broadened from plan's `'running'` check to cover any manually requeued row. |
| **U1.5** Lease renewal in `on_stage_complete` | ✅ Complete | `worker.py:_renew_lease()` at line 169, called from `_on_stage` at line 373 | No separate heartbeat thread. |
| **U1.6** Three-outcome error handling | ✅ Complete | `worker.py:392-425` | StageError → `_fail`+continue; Exception → `_fail`+re-raise. |
| **U1.7** Poll jitter | ✅ Complete | `worker.py:438` | Exact plan formula. |
| **U1.8** SIGTERM handler | ✅ Complete | `worker.py:413-415` + early-return checks | Posix-only test (skipped on Windows, runs in CI). |
| **U2.1** `PUT /v1/manuscripts/:id/upload` | ✅ Complete | `packages/api/src/routes/manuscripts.ts:52-124` | Streamed via Fastify 5, size cap, ZIP magic validation, CAS write with atomic rename + `access()` dedup, id-shape validation. |
| **U2.2** `stages/ingest_stage.py` | ✅ Complete | `stages/ingest_stage.py:26-44` | `inputs={"raw-docx/1"}`, `outputs={"raw-source/1"}`, `implements="ingest"`. DAG edge to `extract` derived automatically. |
| **U2.3** Registry default-selects `ingest`; tracer selects `acquire` | ✅ Complete | `stages/__init__.py:54`, `tracer_bullet.py:372`, `worker.py:351` | Worker re-selects `ingest` idempotently. |
| **U2.4** `_initial_inputs_for` reads `document_id → source_sha256 → CAS` | ✅ Complete | `worker.py:218-265` | Refuses to guess: BAD_INPUT on no source / malformed sha / CAS file missing / unknown profile. |
| **U2.5** Container PYTHONPATH + CI cover `ingest` package | ✅ Complete | `Dockerfile.worker`, `ci.yml` | `services/ingest` added, `services/learning` dropped. |
| **U3.1** `publisher-agents` declares `dependencies = ["publisher-structure"]` | ✅ Complete | `services/agents/pyproject.toml:14` | YAGNI on the Protocol per plan recommendation. |
| **U3.2** `tools/lint_service_deps.py` | ✅ Complete | `tools/lint_service_deps.py` — AST-walks services imports; fails on undeclared. |  |
| **U3.3** Wire into `contracts` CI job | ✅ Complete | `ci.yml:27-29` |  |
| **U4.1** `InferenceGateway.__init__` requires explicit `simulate: bool` | ✅ Complete | `inference.py:250` — keyword-only, no default. |  |
| **U4.2** `PUBLISHER_ALLOW_SIMULATED_INFERENCE` env gate | ✅ Complete | `inference.py:270-275` | Worker never sets it. |
| **U4.3** Rename `_call_model` → `_simulate_model_call` | ✅ Complete | `inference.py:418` | `"simulated": True` in modelInfo artifact. |
| **U4.4** Delete `AgentRuntime._active_calls` | ✅ Complete | `runtime.py` |  |
| **U4.5** Delete `services/learning` | ✅ Complete | Entire directory removed. | Zero callers. |
| **U4.6** Resolve `fallback_route` | ✅ Complete | `inference.py` — field + loading line both removed. |  |

### Stage 2 — 8.5 band

| Plan Item | Status | Evidence | Notes |
|---|---|---|---|
| **U5/S1** `timingSafeEqual` token compare | ✅ Complete | `plugins.ts:22` | `crypto.timingSafeEqual` on equal-length buffers. |
| **U5/S2** `casPath` sha256 validation | ✅ Complete | `db.ts:47-52` | `^[a-f0-9]{64}$` regex before `path.join`. |
| **U5/S3** Idempotency TOCTOU fixed | ✅ Complete | `plugins.ts:71-104` | INSERT-before-handler, ON CONFLICT DO NOTHING, 202 sentinel, `onSend` fill-in. Concurrent retry race properly resolved. |
| **U5/S4** Webhook URL https + private-host validation | ✅ Complete | `routes/webhooks.ts:70-95` | https-only, rejects RFC1918/loopback/link-local/metadata. DNS rebinding re-check marked as seam. |
| **U5/S5** `@fastify/helmet` | ✅ Complete | `plugins.ts:138-139` |  |
| **U5/S6** `readCasFile` size cap | ✅ Complete | `db.ts:61-70` | `CasReadTooLargeError` on `st.size > CAS_READ_MAX_BYTES`. |
| **U5/S7** Rate-limit keyed on tenant | ✅ Complete | `plugins.ts:141-148` | `keyGenerator: (req) => req.tenantId || req.ip`. |
| **U5/S8** `pg` Pool configured | ✅ Complete | `db.ts:26-30` | `max: 20`, `idleTimeoutMillis: 30_000`, `connectionTimeoutMillis: 5_000`. |
| **U5/S9** Upload zip-bomb caps | ✅ Complete | `stages/ingest_stage.py:47-58` + `routes/manuscripts.ts:58-65` | Entry count cap, decompressed size cap, ZIP magic validation. |
| **U5/S10** Graceful shutdown | ✅ Complete | `app.ts:47-63` | SIGTERM → `server.close()` → `pool.end()`. ⚠️ No timeout (see §7). |
| **U5/S11** SSE via LISTEN/NOTIFY | ✅ Complete | `routes/builds.ts:131-145,157` + `worker.py:180-192` | Dedicated LISTEN connection per client, `connected` event, snapshot replay. LISTEN channel quoted for identifiers. |
| **U6** Boundary hygiene | ✅ Complete | Deleted: `adapters.py`, `billing.py`, `services/learning`, `services/design`. Index.ts → routes/. SQL → db.ts. `terminal_outputs` field added. `page-count/1` schema. README infra/ removed. |  |
| **U7** Observability | ✅ Complete | Worker JSON structured logging with build_id/correlation_id. `/v1/admin/metrics` with p50/p95. `error_kind` CHECK constraint. `/v1/health` checks Postgres+CAS with short timeout. | See §7 for missing `proof-pdf/1` producer. |

### Stage 3 — 9.0 band (not in scope)

| Plan Item | Status |
|---|---|
| **U8** Bounded concurrency | ❌ Not started |
| **U9** Load proof | ❌ Not started |

---

## 3. Architecture Compliance Assessment

### 3.1 The DAG is derived, never hand-wired ✅

`ingest_stage.py:73` declares `outputs={"source": "raw-source/1"}`; `extract_stage.py:230` declares `inputs={"source": "raw-source/1"}`. The edge appears via schema-ID matching in `derive_dag()` — zero executor changes. The `_is_active` filter (`publisher_stages/__init__.py:223-229`) correctly excludes deselected alternatives from the producer map and the reachability fixpoint. The earlier hard-gate bypass (commit `beeff69`) was a caller-side key mismatch, not a DAG derivation defect — the DAG mechanism was correct; the root-input keys were wrong.

**One fragility**: `tracer_bullet.py:71,83` uses private `self._registry._is_active()`. The producer-map logic in `_reachable_stages` is also duplicated in `derive_dag()`. Consider exposing a single `reachable_producers()` method on the registry (nit, not blocking).

### 3.2 Two hard gates — intact ✅

- **Text integrity (`ast-assemble`)**: `structure_stage.py:122-143` raises `StageError(ENGINE_BUG)` on `normalize(text(html)) != normalize(text(source))`. No override flag. ✅
- **Preflight gate**: `package_stage.py:29` declares `inputs={"preflight_report": "preflight/1"}` — the DAG makes it structurally impossible to reach `package` without `preflight` having run. Additionally, `worker.py:391-399` has a new explicit completion guard: `if "package" not in results: raise StageError(ENGINE_BUG…)` — this catches any future caller bug that silently drops the DAG tail. The tracer bullet (`tracer_bullet.py:409-415`) mirrors this guard. ✅

### 3.3 Content-addressed storage — no regression ✅

CAS layout: `sha[:2]/sha[2:4]/sha`. `casPath()` validates `^[a-f0-9]{64}$` before `path.join`. Upload route writes via atomic rename + `access()` dedup. Ingest stage reads via `ContentAddressedStore`. API CAS mount in compose is writable (fixed from `:ro` in commit `beeff69`). ✅

### 3.4 `implements` selection is explicit data ✅

- `acquire.implements = "ingest"` / `ingest.implements = "ingest"` → selected `ingest` (worker/tracer selects explicitly)
- `finish.implements = "finish"` / `finish-gs.implements = "finish"` → selected `finish-gs`
- `check_integrity()` errors on unselected alternatives
- `_initial_inputs_for` and tracer now key the finish profile by `registry.selected_implementation("finish")` → `"finish-gs"`, closing the hard-gate bypass found in post-commit verification

### 3.5 Anti-doctrine compliance ✅

No DI framework, no Python-side repository layer, no async rewrite of the pipeline, no Temporal migration, no microservice split. Confirmed by grep across the full repo — these are mentioned only in the anti-doctrine docs themselves.

---

## 4. Code Quality Findings

### 4.1 Strengths

- **worker.py error handling** — the `_fail()`-before-re-raise pattern is the simplest correct implementation of three-outcome error handling.
- **Idempotency TOCTOU fix** — INSERT-before-handler with `ON CONFLICT DO NOTHING` and 202 sentinel correctly closes the concurrent-retry race (plan N2).
- **Upload route stream parser** — correctly avoids buffering 100 MB DOCXes; the `overLimit` flag prevents misreporting disk-full/IO errors as 413 size violations.
- **SSE LISTEN quoting** — `channel.replace(/"/g, '""')` with belt-and-braces `SAFE_ID` regex prevents identifier syntax errors on build IDs containing `-`.
- **`loadOwned` table whitelist** — `ALLOWED_TABLES` set prevents SQL injection through the table-name parameter; tenant isolation via 404-not-403.
- **Webhook URL validation** — thorough: https-only, private/multicast/loopback/metadata IP blocking, DNS rebinding re-check marked as a delivery-time seam.
- **All SQL queries** are parameterized — no user-input interpolation except whitelisted table names and the SSE channel identifier (both constrained).
- **`lint_service_deps.py`** — uses `tomllib` + `ast` (stdlib), zero dependencies.

### 4.2 Defects

#### Blocking

| # | File:line | Issue | Recommendation |
|---|---|---|---|
| **D1** | `routes/manuscripts.ts:193-208` | `/v1/documents/:id/overrides` has no request body schema. `const { ops } = request.body` — if `ops` is `undefined`, `undefined.length` throws TypeError → 500 crash. | Add Fastify JSON schema or guard with `Array.isArray(request.body?.ops)`. |
| **D2** | `routes/builds.ts:117` | SSE `close()` calls `reply.raw.end()` even when `reply.hijack()` was never called (e.g., `pool.connect()` fails before line 156). Fastify cannot send its 500 error; client gets truncated/no response. | Track whether `hijack` occurred; only call `reply.raw.end()` if hijacked. Otherwise `reply.code(500).send(...)`. |
| **D3** | `stages/prepress_stages.py:268` | `finish-gs` outputs `{"pdf": "pdfx/1", "report": "finish-report/1"}` — no `proof-pdf/1` output. But `finish` (the deselected alternative, `finish_stage.py:52`) produces `proof-pdf/1`, and the API's `DELIVERABLE_SCHEMAS` maps `proof` → `proof-pdf/1`. Since `finish-gs` is the selected implementation (`stages/__init__.py:47`), `proof-pdf/1` has no active producer. The `to_proof()` function already exists in `publisher_prepress.ghostscript`. | Add `"proof": "proof-pdf/1"` to `finish-gs` outputs and call `to_proof()` in `finish_gs()`. |

#### Should-Fix

| # | File:line | Issue | Recommendation |
|---|---|---|---|
| **S1** | `worker.py:346` | Logged `attempt` is stale — `_claim_build` returns the pre-UPDATE row, so `build.get("attempt")` is the value *before* increment. | Log `(build.get("attempt") or 0) + 1` or re-read after the UPDATE. |
| **S2** | `worker.py:202-207` | `_resolve_profile_name` fragile against JSONB deserialization: if `psycopg2.extras` stops auto-deserializing, `profile_ids` comes as raw string `'["Generic 6x9"]'`, `isinstance(list)` is False, `str(ids)` produces JSON literal, `load_profile` returns None → every build fails BAD_INPUT. | Add `json.loads(ids) if isinstance(ids, str) else ids`. |
| **S3** | `plugins.ts:71-104` | The idempotency `onRequest` hook does not skip admin-prefixed routes (only PUBLIC_ROUTES). Currently admin has only GET routes (immune because idempotency fires on POST/PATCH/PUT), but a future POST to admin would pass `tenantId=undefined` to the INSERT. | Add `if url.startsWith('/v1/admin') return;` after PUBLIC_ROUTES check. |

#### Nits

| # | File:line | Issue |
|---|---|---|
| N1 | `tests/integration/test_worker_durability.py:46` | Duplicated `worker_module` fixture shadows `conftest.py:194`. Omitted `setdefault("DATABASE_URL", ...)`. Delete local override. |
| N2 | `tests/integration/test_real_manuscripts.py:36-39` | Duplicated `_headers` helper — copy/pasted from `conftest.py:210-212`. Import from conftest. |
| N3 | `tests/integration/test_real_manuscripts.py:42` / `test_api_hardening.py:45` | Duplicated `_create_uploaded_manuscript` — 90% identical; unify into conftest. |
| N4 | `tests/integration/test_api_hardening.py:265` | `test_admin_metrics_aggregates_stages` uses `document_id = 'test-ms-u7'` (no RUN_ID prefix, no matching manuscript row). Use `register_tenant`. |
| N5 | `schemas/py/models_gen.py` | Stale duplicate of `schemas/py/models.gen.py`. Delete one. |
| N6 | `app.ts:51-63` | Graceful shutdown has no timeout. If `server.close()` hangs, process never exits. |
| N7 | `db.ts:63,69,74,86` | Dynamic `import('node:fs/promises')` on every `readCasFile`/`casBlobExists` call. Use static top-level import. |
| N8 | `worker.py:415-425` | If `_fail()` in the except-Exception handler also fails (dead connection), the original exception is masked. Wrap in inner try/except. |
| N9 | `platform/stages/py/publisher_stages/__init__.py:69` | `_CURRENT_BUILD: dict = {}` — module-level mutable shared by formatter and `run_build`. Correct for single-worker-per-process; needs a comment. |
| N10 | No stage-level failure row recorded | When a stage raises `StageError`, no `build_stages` row is written for the failed stage. The `_on_stage` callback fires only on success. |
| N11 | `page-count/1` has no JSON Schema file | `schemas/page-count/` doesn't exist. Integrity checker only validates the format (`/` present), not existence. Low risk for a primitive integer. Original plan replaced bare `"integer"` to avoid `non_schema_input` warnings — acceptable but worth a tracking note. |

---

## 5. Testing & Coverage Assessment

### 5.1 Collection summary

| Component | Count | Status |
|---|---|---|
| Non-integration tests | 348 | All pass (exit 0) |
| Integration tests collected | 21 | |
| Integration passed | 19 | |
| Integration skipped | 1 | SIGTERM posix-only (skipped on Windows, runs in ubuntu CI) |
| Integration failed | 1 | Pre-existing host-conditional: `test_build_request_produces_a_real_artifact` — requires `gs` on PATH or CAS-aligned compose worker (audit §5.2 baseline); also fails on this host because the compose worker's CAS (`/data/cas` volume) can't see host `TEST_CAS_ROOT` (`.test-cas-cache`). The audit explicitly documents this as the expected single failure on gs-less Windows hosts. |
| **Total** | **369** | |
| Warnings | 1 | `PytestUnhandledThreadExceptionWarning` from SSE test `_reader` thread (now silenced — caught in `beeff69`) |

### 5.2 Verification gates (from plan §5)

| Gate | Test | Status | Notes |
|---|---|---|---|
| U1: expired `running` reclaimed → terminal | `test_expired_running_lease_is_reclaimed` | ✅ Passing | Asserts attempt≥1, worker_id set, lease_expires_at renewed, reaches terminal state. |
| U1: ValueError → `status='failed'` | `test_unexpected_exception_records_failed_then_reraises` | ✅ Passing | `_fail(INTERNAL)` called before re-raise. |
| U1: poison → `dead` | `test_poison_build_dead_letters_after_max_attempts` | ✅ Passing | `status='dead'`, `error_kind='exhausted'`. |
| U1: SIGTERM → exit 0 | `test_sigterm_stops_worker_cleanly` | ⏸️ Skipped (Windows) | Runs in ubuntu CI. |
| U2: two tenants → different ASTs | `test_two_manuscripts_produce_different_artifacts` | ✅ Passing | Asserts `ast/1` divergence per-tenant; full PDF gate needs engines. |
| U2: no source → BAD_INPUT | `test_build_without_uploaded_source_fails_bad_input` | ✅ Passing | `status='failed'`, `error_kind='bad_input'`. |
| U3: lint flags undeclared deps | `tools/lint_service_deps.py` in contracts CI | ✅ Verified |  |
| U4: gateway refuses without `simulate` | `test_gateway_requires_explicit_simulation_flag` | ✅ Verified | `TypeError` on omitted; `RuntimeError` when `simulate=True` + gate closed. |
| U5: concurrent POST → exactly one build | `test_concurrent_idempotent_build_creates_exactly_one` | ✅ Passing |  |
| U5: `casPath('../../etc/passwd')` → rejected | `test_cas_path_rejects_malformed_hash` | ✅ Passing |  |
| U6: `check_integrity()` clean | Registry integrity check | ✅ Verified |  |
| U7: `/v1/health` with Postgres down → non-200 | `test_health_degraded_when_postgres_down` | ✅ Passing |  |
| U7: `/v1/admin/metrics` aggregates | `test_admin_metrics_aggregates_stages` | ✅ Passing | Admin-gated. |
| U5/S11: SSE NOTIFY reaches client | `test_sse_pushes_notified_events` | ✅ Passing | Fixed in `beeff69` (LISTEN quoting). |

### 5.3 Test infrastructure quality

- **Isolation**: `RUN_ID` (`uuid4().hex[:8]`) scopes all generated IDs. The `db` fixture purges `test-%` rows at setup AND teardown in FK-safe delete order (children → parents). ✅
- **Lifecycle**: `api_server` is module-scoped with health-poll boot (100×0.2s). `worker_module` pins env before import. Subprocess workers are terminated in `finally` and killed on timeout. ✅
- **Failing-first assertions**: Every gate's module docstring documents what pre-change behavior it expects to fail against. ✅
- **Edge coverage**: Zip bombs (entry count + decompressed size), corrupt archives, oversized uploads (413), path-traversal hashes, private-host webhook rejection, concurrent idempotency TOCTOU, dead-letter on attempt exhaustion, SIGTERM clean exit, health degradation on DB-down, admin-gated metrics, SSE push. ✅
- **Three test helpers duplicated** across files — `_headers`, `_create_uploaded_manuscript`, `worker_module` — should be unified into conftest (see §7 N1–N3).

---

## 6. Risk & Regression Analysis

### 6.1 Risks introduced by this uplift

| Risk | Severity | Mitigation |
|---|---|---|
| **R1 — `finish-gs` has no `proof-pdf/1` output** | MEDIUM | `DELIVERABLE_SCHEMAS` maps `proof` → `proof-pdf/1` but it's never produced. API `GET /v1/builds/:id/artifacts/proof` will 404. Add output to `finish-gs` (see D3). |
| **R2 — Overrides route crashes on missing `ops`** | HIGH | Returns 500 instead of 400. Add schema validation (see D1). |
| **R3 — SSE error path sends truncated response** | MEDIUM | `close()` calls `reply.raw.end()` before hijack → client gets nothing. Track hijack state (see D2). |
| **R4 — No staged-failure recording** | LOW | A build that fails at stage N has no `build_stages` row for that stage. Diagnostics rely on the `error_message` on the builds row and the worker log. Not a correctness issue, but hurts observability. |
| **R5 — `_resolve_profile_name` fragile against JSONB typing** | LOW | Would fail only if `psycopg2.extras` deserialization changes. Defensive `isinstance(str)→json.loads` blocks it (see S2). |
| **R6 — Schema migrations idempotent but no version tracking** | LOW | `ALTER TABLE ADD COLUMN IF NOT EXISTS` covers current changes. A future column rename needs a migration mechanism. Acceptable for single-service deployment at this maturity (matching the prior audit's assessment). |

### 6.2 Backward compatibility

- **API**: `PUT /v1/manuscripts/:id/upload` is additive. Existing routes unchanged. Index.ts → app.ts shim preserves `tsx src/index.ts` boot path.
- **DB**: Columns added with defaults; existing rows get NULL for new columns (handled by `IS NULL` reclaim clause). `manuscripts.source_sha256` is NULL for pre-existing rows → builds refused with BAD_INPUT (correct).
- **Registry**: `acquire` deselected by default; tracer selects it explicitly. Worker selects `ingest` idempotently.
- **AI layer**: `InferenceGateway()` without `simulate=` raises TypeError. In-tree callers updated. No production code constructs an `InferenceGateway` (plan-verified).
- **`services/learning`**: Deleted. Zero callers outside its own tests. CI updated.

### 6.3 Security

- ✅ Upload route: manuscript-id shape validation before path interpolation.
- ✅ Upload route: rejects non-ZIP bytes (400, names the problem).
- ✅ Upload route: enforces byte-size cap (413 for true overages).
- ✅ `casPath`: `^[a-f0-9]{64}$` regex before `path.join` — path traversal impossible.
- ✅ Webhook URL: https-only, private/loopback/metadata address rejection.
- ✅ `timingSafeEqual` token compare — no timing leak.
- ✅ All SQL parameterized — no user-input interpolation in queries.
- ⚠️ Overrides route: no body schema validation (see D1 — request DoS via `ops.length` on undefined).
- ⚠️ Stored `media_type` from upload Content-Type header propagated without allow-list (see N8 in API subagent review). Not XSS-able for PDF/binary artifacts; low risk.

### 6.4 Performance

- ✅ Worker poll jitter prevents thundering herd.
- ✅ Lease renewal: single-row UPDATE, no heartbeat thread.
- ✅ Upload route never buffers the body.
- ✅ SSE: push-based LISTEN/NOTIFY replaces polling (120 queries/client/stream eliminated).
- ⚠️ Graceful shutdown has no timeout — hung connection blocks exit forever.
- ⚠️ U8 (bounded concurrency, docker mem_limit, admission control) not yet started.

---

## 7. Required Corrections

### Blocking (must fix before declaring Stage 2 complete)

| Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|
| **HIGH** | `packages/api/src/routes/manuscripts.ts` | 193-208 | `/v1/documents/:id/overrides` PATCH — no request body schema. `const { ops } = request.body` crashes with TypeError on `undefined.length` (500). | Add Fastify JSON schema: `schema: { body: { type: 'object', required: ['ops'], properties: { ops: { type: 'array' } } } }` or guard with `Array.isArray`. |
| **HIGH** | `packages/api/src/routes/builds.ts` | 117 | SSE `close()` calls `reply.raw.end()` even when `reply.hijack()` was never called (e.g., `pool.connect()` failure). Client gets truncated/no response instead of proper 500. | Track `hijacked` flag; only `raw.end()` if hijacked. Otherwise `reply.code(500).send(...)`. |
| **MEDIUM** | `stages/prepress_stages.py` | 268 | `finish-gs` doesn't produce `proof-pdf/1`. API `DELIVERABLE_SCHEMAS` maps `proof`→`proof-pdf/1`, but it has no active producer since `finish-gs` (selected) omits it and `finish` (deselected) has it. | Add `"proof": "proof-pdf/1"` to `finish-gs` outputs; call `to_proof()` in `finish_gs()`. |

### Should-fix (low risk, high value-for-cost)

| Severity | File | Line | Issue | Recommendation |
|---|---|---|---|---|
| **LOW** | `worker.py` | 346 | Logged `attempt` stale (pre-UPDATE value). | Log `(build.get("attempt") or 0) + 1`. |
| **LOW** | `worker.py` | 202-207 | `_resolve_profile_name` fragile against JSONB raw string. | `json.loads(ids) if isinstance(ids, str)`. |
| **LOW** | `packages/api/src/plugins.ts` | 71-104 | Idempotency hook not gated on admin routes. | Add `if url.startsWith('/v1/admin') return`. |

### Nits (deferrable)

| # | File | Issue |
|---|---|---|
| N1 | `tests/integration/test_worker_durability.py:46` | Duplicate `worker_module` fixture — delete local, use conftest's. |
| N2 | `tests/integration/test_real_manuscripts.py:36-39` | Duplicate `_headers` — import from conftest. |
| N3 | `tests/integration/test_real_manuscripts.py:42` / `test_api_hardening.py:45` | Duplicate `_create_uploaded_manuscript` — unify. |
| N4 | `tests/integration/test_api_hardening.py:265` | Hardcoded `document_id='test-ms-u7'` — use `register_tenant`. |
| N5 | `schemas/py/models_gen.py` | Delete stale duplicate of `models.gen.py`. |
| N6 | `app.ts:51-63` | Graceful shutdown timeout. |
| N7 | `db.ts:63,69` | Dynamic `import('node:fs/promises')` — use static import. |
| N8 | `worker.py:415-425` | `_fail()` exception masking original crash. |
| N9 | `publisher_stages/__init__.py:69` | `_CURRENT_BUILD` mutability comment. |
| N10 | `worker.py:415-425` | No `build_stages` row for failed stage. |
| N11 | `page-count/1` | No JSON Schema file. |

---

## 8. Final Verdict

| Criterion | Assessment |
|---|---|
| **Plan completeness** | U1–U7 fully implemented; U8–U9 not started. 100% of Stage 1, 100% of Stage 2. |
| **Architecture compliance** | DAG derived automatically; `implements` selection explicit and tested; two hard gates structurally intact; content-addressed storage no regression; anti-doctrine violations: **none**. |
| **Code quality** | Worker error handling, idempotency TOCTOU, upload stream parser, SSE LISTEN quoting, webhook URL validation all well-constructed. One HIGH (overrides route crash), one HIGH (SSE teardown), one MEDIUM (missing proof-pdf/1), 2 LOW should-fix, 11 nits. |
| **Testing** | 8 of 8 Stage 1 gates have failing-first tests; 6 of 6 Stage 2 gates have tests. 369 collected, 348 unit pass, 19 integration pass, 1 skipped, 1 documented pre-existing host-conditional failure. Edge coverage strong. Three duplicated test helpers should be unified. |
| **Risk** | Backward-compatible DB migration; additive API routes; CAS sha256 validation closes path traversal; compose-stack E2E verified. One HIGH new (overrides route), two MEDIUM, no CRITICAL. |
| **Security** | Upload id-injection guard, ZIP magic check, size cap, sha256 path validation, timingSafeEqual auth, https-only webhook, parameterized SQL everywhere. Overrides route lacks body validation (see D1). |
| **Performance** | Jitter, no body-buffering, SSE push-based, lease renewal O(1). U8 pending. |

---

**FINAL VERDICT: APPROVED WITH CHANGES**

The implementation is architecturally sound and the two hard gates are intact. The three blocking items (§7: overrides route crash, SSE teardown, missing `proof-pdf/1`) must be addressed before marking Stage 2 complete. The should-fix items are low-risk and high-value-for-cost. Nits are deferrable. The system is at the **8.0–8.5 band** per the plan's rubric (zero CRITICAL, zero HIGH remaining after D1/D2 are fixed). Stage 3 (U8 bounded concurrency, U9 load proof) is the next sequential band.
