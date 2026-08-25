# Architecture Uplift Plan — 6.0 → >8.5

**Status:** Stage 1 (U1–U4) **implemented and tested** as of 2026-08-10;
Stage 2 (U5–U7) **implemented** in the same working tree as of 2026-08-10
(API hardening, boundary hygiene, observability — not yet audit-reviewed,
verification gates require Postgres at `:55432` / the compose stack's engines).
Stage 3 (U8–U9) **partially implemented** as of 2026-08-14: container resource
limits, per-tenant admission control, 429-aware retry, and both multi-worker
tests are written — see §3 U8/U9 and §5 for exactly what is proven versus
still asserted. Docker-independent verification was done against a local
Postgres (see the workstream sections for the exact gates and their host
requirements — the SIGKILL/PDF-level gates need the compose stack's engines).
**Baseline:** architecture audit 2026-08-10 (score 6/10, maturity Early Production)
**Supersedes nothing.** Continues `ARCHITECTURE_REMEDIATION.md` (A0–A2 landed, A3–A5 open)
and inherits its numbering discipline: this document owns the **U-series** (U1–U9).

Epistemic labels carried from the audit: `[VERIFIED]` source-confirmed ·
`[HYPOTHESIS]` inferred · `[UNKNOWN]` insufficient evidence.

---

## 0. How the score actually moves

The score is a rubric, not a sum. Points do not accumulate — **bands gate.** You cannot
buy your way to 8.5 with observability while a CRITICAL is open.

| Band | Rubric requirement | Blocking workstreams |
|---|---|---|
| **6.0 → 8.0** | Zero CRITICAL, zero HIGH violations | U1, U2, U3, U4 |
| **8.0 → 8.5** | ≤1 MEDIUM, system is *observable* and *testable* | U5, U6, U7 |
| **8.5 → 9.0+** | Scalability **demonstrated**, not asserted; supply chain controlled | U8, U9 |

The audit's rubric reads 8 as "minor drift in 1–2 modules, no critical violations" and 10 as
"all layers correctly separated, patterns consistent, observable, testable, scalable."
The gap between 8 and 9 is almost entirely **evidence**: today the project asserts it scales
(`FOR UPDATE SKIP LOCKED`) but has never run two workers against one queue under load.

**Ceiling note.** 10/10 is not reachable while `docs/ARCHITECTURE.md` describes a 21-stage
pipeline with Temporal orchestration, three DesignSpec emitters and a cross-emitter agreement
gate, of which one emitter exists. That drift is *planned* (A5, sanctioned at
`ARCHITECTURE.md:199`), so it caps the score around 9.0–9.3 rather than counting as a
defect. Closing it is a product decision, not a remediation.

---

## 1. New findings — not in the audit

Two of these outrank the audit's single CRITICAL. They were found by reading `worker.py`
and `packages/api/src/index.ts` line by line rather than by structural analysis.

### N1 — Every production build renders the same fixture manuscript `[VERIFIED]` — **CRITICAL**

`worker.py:75-94`, `_initial_inputs_for(build)`, ignores its `build` argument entirely and
returns a hardcoded `corpus/manuscripts/minimal-novel.ast.json` with `profile_name:
"Generic 6x9"`. The API's `POST /v1/builds` correctly validates that `documentId` belongs to
the caller (`index.ts:347-350`), writes a tenant-scoped row, and returns a build id — and
then the worker builds a demo novel instead, for every tenant, on every build.

The function's own docstring is honest about being a placeholder pending A2. But A2 is
recorded as **landed** in `ARCHITECTURE_REMEDIATION.md:376-391`, so the placeholder is now
load-bearing in a path documented as complete. Consequences, in order of severity:

1. A paying tenant's build returns a **different book than they submitted**, with a
   `status='completed'` and a green preflight verdict attesting to it.
2. Because CAS is content-addressed and the input is identical for every build, every
   tenant's artifacts **deduplicate to the same blobs**. Tenant isolation holds at the
   Postgres row level (`artifacts.build_id`) but the bytes are literally shared.
3. `docs/PRODUCTION_READINESS.md`'s decisive gate — "manuscript-integrity / no-loss
   guarantee" — cannot be evaluated at all, because no user manuscript reaches the pipeline.

The two hard gates are working exactly as designed here and cannot help: text integrity
compares the rendered HTML against *the AST it was given*, and that AST is the fixture's.
A gate can only verify the input it receives.

### N2 — Idempotency has a TOCTOU race `[VERIFIED]` — **HIGH**

`index.ts:127-142` checks `idempotency_keys` on `onRequest`; `index.ts:144-159` inserts the
key on `onSend`, i.e. *after* the handler has already run and created the resource. Two
concurrent retries of the same `POST /v1/builds` both miss the cache, both create a build,
and the second `INSERT ... ON CONFLICT DO NOTHING` silently discards the second response
record. The mechanism protects against sequential retries — the common case — and fails
open under exactly the concurrent-retry conditions a client-side retry storm produces.

### N3 — SSE endpoint is a self-inflicted database load generator `[VERIFIED]` — **MEDIUM**

`index.ts:419-439` polls in a `while` loop every 500 ms for 30 s, issuing **two** queries per
iteration — 120 queries per connected client per stream. Fifty dashboard tabs is 6,000
queries/minute against `builds` and `build_stages` for data that changes a handful of times
per build. `[HYPOTHESIS]` this saturates the connection pool (`pg` default max 10, never
configured at `index.ts:42`) well before the worker tier becomes the bottleneck.

### N4 — Unbounded CAS read into API memory `[VERIFIED]` — **MEDIUM**

`readCasFile` (`index.ts:548-551`) does `fs.readFile(..., 'utf-8')` with no size cap, then
`JSON.parse`. Called on `ast/1` (`index.ts:298`) and `preflight/1` (`index.ts:463`). A
900-page manuscript's AST — the exact fixture `BUILD_PLAN.md` says will force streaming
(D4) — is loaded whole into the API process per request, with no limit and no streaming.
The download route already does this correctly with `createReadStream` (`index.ts:544`).

### N5 — `casPath` does not validate its input `[VERIFIED]` — **LOW** (defence in depth)

`casPath` (`index.ts:49-51`) does `path.join(CAS_ROOT, sha.slice(0,2), sha.slice(2,4), sha)`
with no `^[a-f0-9]{64}$` check. The value comes from the `artifacts` table, written by the
worker from real CAS hashes, so there is no reachable path traversal today `[VERIFIED]`.
It is one bad INSERT away from being one, and the check is one line.

---

## 2. Paradigm doctrine — the target shape per module

The user asked for the *optimal* paradigm per module. The honest answer for the core is
**it is already correct and must not be touched**; the value is in naming why, so that
future work does not erode it.

| Module | Paradigm | Patterns | Verdict |
|---|---|---|---|
| `platform/stages` (registry) | **Declarative + pure functional** — stages are data; the graph is derived by a total function over that data | Registry · Declaration-as-data · Derived graph · Fixpoint reachability | ✅ **Keep unchanged.** This is the best-engineered thing in the repo. `derive_dag()` (`__init__.py:224-249`) is referentially transparent; `check_integrity()` (`:287-427`) is a real total validator with cycle detection. Only change: assert `StageDeclaration` is `@dataclass(frozen=True)` so immutability is enforced, not conventional. |
| `stages/*.py` | **Imperative shell over functional core** (D1) | Command · Ports & Adapters (constructor-injected) | ✅ Keep. `ImageGenPort` injection in `cover_stages.py` is the correct existing template — apply it to any new external dependency rather than importing a client directly. |
| `worker.py` | **Explicit state machine + transactional job processor** — currently an implicit state machine with unreachable states | Lease/heartbeat · Dead-letter queue · Bulkhead · Circuit breaker | ❌ **U1.** The single biggest structural change in this plan. |
| `services/*` (core) | **Functional core** — pure functions over immutable data, no I/O | Hexagonal, ports defined by the *consumer* not the provider | ⚠️ Mostly right; one undeclared cross-service import (U3). Formalise ports as `typing.Protocol` per `~/.claude/rules/python/patterns.md`. |
| `services/agents` | **Strategy + explicit Null Object** | Strategy (per-role runner) · **Null Object** · Capability-scoped tool surface | ⚠️ **U4.** The stub is not the problem; the stub being *indistinguishable from a real implementation* is. Make it a named `NullAgentRuntime` that a production config refuses to load. |
| `services/structure/inference.py` | **Strategy + Chain of Responsibility** for the model cascade | Chain of Responsibility (Haiku→Sonnet→Opus) · Circuit Breaker (already real) | ⚠️ **U4.** `fallback_route` is loaded and never read (`inference.py:84,228`) — the Chain has links but no traversal. |
| `packages/api` | **Thin adapter / Repository** | Repository (extract `pool.query` calls) · Middleware chain · Resource-per-module routing | ⚠️ **U5/U6.** 615 lines, one file, SQL inline in handlers. |
| `platform/cas` | **Immutable content-addressed store** | Value Object (the hash *is* the identity) | ✅ Keep. Add boundary validation (N5). |
| `services/learning` | — | — | ⚠️ **U4.** Fully built, zero callers. Decide: wire or delete. |

**Anti-doctrine — things this plan explicitly refuses to do.** No dependency-injection
framework (constructor injection already works). No repository layer for the Python side
(CAS *is* the repository). No async rewrite of the pipeline (batch PDF rendering is
CPU-bound; async buys nothing and costs the executor's simplicity — see U8 for what to do
instead). No Temporal migration (A5, deferred by design). No microservice split (the
hidden-monolith finding is *appropriate* at this scale; the fix is renaming expectations,
not splitting containers).

---

## 3. Workstreams

### U1 — Worker durability: make every claimed build reach a terminal state
**Closes:** audit CRITICAL (temporal coupling / underengineering) · **Band gate:** 8.0

Three defects compound into one failure mode:

- `run_build` catches only `StageError` (`worker.py:185`). Anything else — including
  `_record_artifact`'s own `RuntimeError` (`worker.py:128`) — propagates out of
  `run_build`, out of `main`'s `try`, and kills the process.
- `_claim_build` selects only `status='queued'` (`worker.py:58`), so a row left at
  `'running'` is never reclaimed by any worker, ever.
- There is no lease, heartbeat, or timeout. A `docker kill` mid-build is indistinguishable
  from a build that is still working.

**Target design — explicit state machine with a lease:**

```sql
-- platform/db/schema.sql
ALTER TABLE builds ADD COLUMN attempt        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE builds ADD COLUMN lease_expires_at TIMESTAMPTZ;
ALTER TABLE builds ADD COLUMN worker_id      TEXT;
-- status: queued | running | completed | failed | dead
CREATE INDEX IF NOT EXISTS builds_reclaimable_idx
    ON builds (lease_expires_at) WHERE status = 'running';
```

The claim query becomes reclaim-aware — one statement, no new moving parts:

```sql
SELECT * FROM builds
WHERE status = 'queued'
   OR (status = 'running' AND lease_expires_at < now())
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT 1
```

and the claim sets `attempt = attempt + 1`, `worker_id`, `lease_expires_at = now() +
interval '10 minutes'`. The existing `on_stage_complete` hook (`worker.py:158`) is already
called after every stage — extend it to renew the lease, so a long but healthy build never
expires while a dead one always does. No separate heartbeat thread.

**Error handling — three outcomes, not one:**

```python
except StageError as e:          # expected: a gate refused, or bad input
    _fail(conn, build_id, e.kind.value, e.message)
except Exception as e:           # unexpected: bug, OOM, disk full
    _fail(conn, build_id, "INTERNAL", f"{type(e).__name__}: {e}")
    raise                        # after recording, let the process die loudly
```

Recording *before* re-raising is the whole point: the build reaches a terminal state even
though the process does not survive. A `MAX_ATTEMPTS = 3` check at claim time moves a
build to `'dead'` (dead-letter) rather than looping forever on a poison input.

**Also in U1** (small, same file, same test run):
- Jitter the poll: `time.sleep(POLL_INTERVAL_S * (0.5 + random.random()))`. Ten workers
  polling on a fixed 2 s tick is a synchronised thundering herd against one row.
- SIGTERM handler: finish the current build, do not claim another, exit 0. Without it every
  `docker compose up -d --build` orphans an in-flight build for a full lease period.

**Proof:** an integration test that starts a build, `SIGKILL`s the worker mid-stage, starts a
second worker, and asserts the build reaches `completed` on the reclaim — plus a unit test
that a stage raising `ValueError` (not `StageError`) leaves `status='failed'`, not
`'running'`. Both fail against today's code.

---

### U2 — Build what the tenant actually submitted
**Closes:** N1 (CRITICAL) · **Band gate:** 8.0 · **Depends on:** nothing — start here

This is the largest *functional* gap and the one with real-world consequence: a customer
receives someone else's book. Four steps, in order:

1. **Upload path.** `POST /v1/titles/:id/manuscripts` returns
   `uploadUrl: /v1/manuscripts/:id/upload` (`index.ts:261`) — a URL with **no route behind
   it** `[VERIFIED]`. Implement it: stream the request body to CAS, cap it (see U5/S9),
   record `manuscripts.source_sha256`. Do not route the bytes through the JSON body —
   `bodyLimit` is 1 MiB (`index.ts:39`) and a DOCX is larger.
2. **Ingest wiring.** `services/ingest.docx_to_ast` exists and is tested but no stage calls
   it `[VERIFIED]`. Add an `ingest` stage: `inputs={"docx_path": "raw-docx/1"}` →
   `outputs={"source": "raw-source/1"}`. The DAG derives the new edge to `extract`
   automatically — no executor change, which is the registry design paying off.
3. **Real inputs.** Rewrite `_initial_inputs_for` to read `build["document_id"]` →
   `manuscripts.source_sha256` → CAS path, and `build["profile_ids"]` → profile name. Its
   docstring already says "Replace this function's body — not its callers"; that holds.
4. **Refuse to guess.** If the document has no stored source, raise `StageError(BAD_INPUT)`.
   Never fall back to a fixture. This is the same lesson as the `acquire` stage's silent
   fixture substitution, fixed in commit `7a4401b` — do not reintroduce it one layer up.

**Proof:** upload two different manuscripts under two tenants, build both, assert the
output PDFs differ and each matches its own input's text. Today both would be byte-identical
— which is itself the cleanest possible regression test for this defect.

---

### U3 — Declare the `services/agents` → `services/structure` dependency
**Closes:** audit HIGH (coupling) · **Band gate:** 8.0 · **Effort:** ~15 minutes

`structure_wrangler.py:85` imports `publisher_structure.rules` inside a function while
`services/agents/pyproject.toml:10` declares `dependencies = []`. It resolves only because
`Dockerfile.worker` puts every package on one `PYTHONPATH`.

Two honest options — pick by whether the dependency is intentional:

- **It is intentional** (agents legitimately reuse structure rules): declare
  `dependencies = ["publisher-structure"]` in the manifest. Cost: one line. Benefit: CI
  breaks loudly if packaging ever changes, instead of production breaking quietly.
- **It is incidental** (the wrangler needs *a* classifier, not *that* one): define a
  `Classifier` Protocol in `publisher_agents` and inject an implementation. Correct per
  hexagonal doctrine — the consumer owns the port.

Recommend the first. The second is the better architecture and the wrong trade today:
one caller does not justify an interface (YAGNI), and the declaration closes the actual
risk. Revisit when a second implementation appears.

**Proof:** `tools/lint_service_deps.py` — walk each `services/*/pyproject.toml`, AST-parse
its package for `import publisher_*`, fail if an import is not declared. Wire into the
existing `contracts` CI job next to `lint_stage_versions.py`. This converts a one-time fix
into a permanent invariant, which is what actually moves the score.

---

### U4 — Resolve the AI layer: wire it or quarantine it
**Closes:** audit HIGH (fake orchestration) · **Band gate:** 8.0

Today the AI surface is **0% functional and ~80% architecturally complete** — a genuinely
unusual failure mode. `ToolSurface` capability scoping is real and adversarially tested
(`test_agents.py:283-288`). `policy.yaml` is real policy-as-data. The circuit breaker in
`inference.py:328-388` is real. Behind all of it, `_call_model` returns
`{"classification": "paragraph", "confidence": 0.85}` unconditionally (`inference.py:390-436`).

The score is not damaged by the absence of LLM calls. It is damaged by **a fake being
shaped exactly like the real thing**, so nothing in the type system, the tests, or the
config prevents a future change from shipping fabricated classifications as real ones.

**Phase A — quarantine (do now, cheap, closes the HIGH):**

- Rename `_call_model` → `_simulate_model_call`; make `InferenceGateway` require an
  explicit `simulate: bool` constructor argument with no default.
- Add a `PUBLISHER_ALLOW_SIMULATED_INFERENCE` env gate, default off, that the worker never
  sets — exactly the `allow_stub_engines` pattern (`tracer_bullet.py:40`, `worker.py:150`),
  which already works and which the team already understands.
- Emit `simulated: true` into any artifact derived from a simulated call, so a fabricated
  confidence is visible in the build record rather than inferred from source reading.
- Delete `AgentRuntime._active_calls` (`runtime.py:229`) — written, never read.
- Decide `services/learning`: wire it into `AgentRuntime` or delete the package. A fully
  tested module with zero callers is a maintenance liability that reads as a capability.
  Recommend **delete**, recover from git when P7 actually starts.
- Resolve `fallback_route`: implement the Chain of Responsibility traversal, or drop the
  field. Do not leave dead config that documents an unimplemented behaviour (A3.4).

**Phase B — activation (only when P2 starts, and strictly in this order):**

1. Schema-validate agent output *before* it reaches the override log (A3.5). This must land
   **before** any real model call — it is the only thing standing between non-deterministic
   output and the override state, and D9 ("model output is frozen data, never a live
   decision") depends on it entirely.
2. Then wire one real provider behind `_call_model`, using the existing cost-ceiling
   breaker as the safety net.
3. Then add bounded concurrency (U8) — before, not after, the first live call.

**Do not wire `OpenRouterImageGenAdapter` yet.** It is real, retry-capable, credential-
bearing, and unreferenced by production code (`image_gen_port.py:110-136`). It also ships a
pre-built defect: `cover_stages.py:197-236` dispatches it in a bare `for` loop with no
semaphore, and `_call` retries 5xx but not 429. Wiring it as-is converts a dormant
component into a rate-limit incident. Fix the loop and 429 handling in the same PR that
wires it, or leave it dormant.

---

### U5 — API hardening
**Closes:** N2, N3, N4, N5 + security items below · **Band gate:** 8.5

The API is in better shape than the audit implied: bearer auth with tenant resolution
(`index.ts:107-120`), tenant checks on every resource load, 404-not-403 to prevent
existence disclosure (`index.ts:230-232`), `randomBytes` for ids and webhook secrets, CORS
failing closed to `false` when unconfigured (`index.ts:173`), parameterised SQL everywhere
`[VERIFIED — every `pool.query` reviewed]`. Real credit where due; the gaps are specific.

| ID | Fix | Severity |
|---|---|---|
| **S1** | `resolveTenant` (`index.ts:96-103`) compares tokens with `===` — non-constant-time. Use `crypto.timingSafeEqual` on equal-length buffers. Tokens also live in a comma-separated env var; document the OIDC swap as the P8 exit, keep the seam. | MEDIUM |
| **S2** | Validate `^[a-f0-9]{64}$` in `casPath` before `path.join` (N5). One line, kills a whole class. | LOW |
| **S3** | Idempotency TOCTOU (N2): reserve the key **before** the handler with `INSERT ... ON CONFLICT DO NOTHING`; if the insert affects 0 rows, the request is a duplicate — return the stored response, or `409` if the original is still in flight. Fills in the body on `onSend` as now. | HIGH |
| **S4** | Webhook `url` accepts any URI (`index.ts:563`). Before delivery ships: allow-list `https` only, resolve the host, reject RFC1918/loopback/link-local/metadata addresses, re-check after DNS resolution at delivery time (DNS rebinding). Delivery is not implemented yet — **this is the cheapest moment in the project's life to get it right.** | HIGH *(when delivery lands)* |
| **S5** | No security headers. Register `@fastify/helmet`. | LOW |
| **S6** | Cap `readCasFile` (N4): `stat` first, refuse over a configured ceiling, stream + incremental parse above it. | MEDIUM |
| **S7** | `@fastify/rate-limit` keys on IP by default (`index.ts:176-179`); one tenant behind one NAT exhausts the shared bucket while a distributed caller bypasses it. Key on `request.tenantId`, fall back to IP for unauthenticated routes. | MEDIUM |
| **S8** | Configure the `pg` Pool (`index.ts:42`): `max`, `idleTimeoutMillis`, `connectionTimeoutMillis`. Default max 10 with the SSE loop of N3 is a lock-up waiting for traffic. | MEDIUM |
| **S9** | When upload lands (U2): cap upload size, cap decompressed DOCX size and entry count (zip bomb), confirm `python-docx`'s `lxml` parser resolves no external entities (XXE). `python-docx` is on `lxml`, which disables entity resolution by default — **verify, do not assume** `[UNKNOWN — not verified this pass]`. | HIGH *(when upload lands)* |
| **S10** | No graceful shutdown (`index.ts:607-613`). Add SIGTERM → `server.close()` → `pool.end()`. Every deploy currently drops in-flight requests, including 30-second SSE streams. | MEDIUM |
| **S11** | Replace the SSE poll loop (N3) with Postgres `LISTEN/NOTIFY` — the worker already writes `build_stages` on every stage (`worker.py:158`); add `NOTIFY build_<id>` there. Removes 120 queries per client per stream and makes progress genuinely push-based. | MEDIUM |

**Cross-tenant CAS dedup `[HYPOTHESIS]` — flagged, deliberately not fixed.** Content-
addressing means two tenants uploading the same manuscript share one blob. A tenant who
guesses a hash cannot read it (every route is `build_id`-scoped and tenant-checked
`[VERIFIED]`), but *timing* on a build that dedups against an existing blob could confirm
that content exists in the system. This is inherent to content-addressed storage, the
exposure is a confirmation oracle rather than disclosure, and the mitigations (per-tenant
namespacing, keyed hashing) each cost the dedup that makes the cache worth having.
Recommend accepting with a written note, revisiting if a tenant's threat model requires it.

---

### U6 — Boundary hygiene
**Closes:** audit MEDIUM ×2 + LOW ×2 · **Band gate:** 8.5

- **`packages/api/adapters.py` + `billing.py`** — Python business logic (Prince/InDesign
  rendering adapters, quota enforcement) inside a TypeScript deployment package, imported by
  nothing, tested by `packages/api/tests/test_platform.py`. This is A4, already known.
  Move to `services/` with a declared boundary, or delete. Recommend **delete** — the
  rendering adapters duplicate concerns `stages/paginate_stage.py` now owns properly, and
  keeping a second unwired renderer invites divergence.
- **`services/design/publisher_design/`** — an empty directory. Delete it or put the design
  service in it. An empty package that appears in the services inventory reads as a
  capability that exists.
- **Orphan stage outputs** — `integrity-report/1`, `pagemap/1`, `finish-report/1`,
  `proof-pdf/1` are produced and never consumed. All four are legitimately *terminal
  deliverables* (the API serves three of them via `DELIVERABLE_SCHEMAS`, `index.ts:67-79`),
  so the right fix is a `terminal_outputs` field on `StageDeclaration` that
  `check_integrity()` treats as an intended sink. This turns four standing warnings into a
  declaration — and keeps the check sharp for a genuinely orphaned output later.
- **`cover` stage's `page_count: "integer"`** (`prepress_stages.py:132`) is not a schema ID
  and the registry's own check flags it (`__init__.py:413-425`). Give it a real schema
  (`page-count/1`) so the integrity check can run clean and stay meaningful.
- **`index.ts` at 615 lines** — split by resource (`routes/titles.ts`,
  `routes/builds.ts`, `routes/artifacts.ts`, `routes/webhooks.ts`) and extract SQL into a
  repository module. Not cosmetic: the tenant check is currently re-implemented inline at
  six call sites, and the one place it was originally *missing* (manuscript creation,
  `index.ts:246-250`) is exactly the kind of omission that a single `loadOwned()` helper
  makes structurally impossible.
- **README's `infra/` directory does not exist** `[VERIFIED]`. Delete the reference or
  create the directory. Documentation that claims k8s/terraform where there is none is the
  same class of defect as the code findings above — a false claim of capability.

---

### U7 — Observability
**Band gate:** 8.5 — the rubric names it explicitly

Today: `print()` to stdout in the worker, Fastify's default JSON logger in the API, no
correlation between them, no metrics. When a build fails you learn the stage and the
message; you cannot answer "how long does `paginate` take at p95" or "which stage fails
most often" without reading Postgres by hand.

- Replace `print()` in `worker.py` with `logging`, JSON formatter, one line per event,
  `build_id` on every record. (Also satisfies `~/.claude/rules/python/hooks.md`.)
- Propagate a request/build correlation id: API generates it on `POST /v1/builds`, stores it
  on the row, worker puts it in every log record. One id joins an HTTP request to a
  container's stdout.
- `build_stages` already stores `duration_ms` and `metrics` per stage `[VERIFIED]` — the
  data for stage-level p50/p95 is **already being collected** and simply never read. A
  read-only `/v1/admin/metrics` endpoint aggregating it is a few hours and needs no schema
  change. Start there before reaching for Prometheus.
- Structured failure taxonomy: `StageError.kind` is already an enum `[VERIFIED]`; make
  `builds.error_kind` a constrained set so failure-mode counts are queryable.
- Health endpoint (`index.ts:183`) returns `status: 'ok'` unconditionally — it does not
  check Postgres or CAS. A load balancer routing on it will happily route to an instance
  whose database is gone. Make it check both, with a short timeout.

---

### U8 — Bounded concurrency and backpressure
**Band gate:** 9.0

No semaphore, thread pool, or queue bound exists anywhere in `services/*` or `platform/*`
`[VERIFIED, self-acknowledged at ARCHITECTURE_REMEDIATION.md:242]`. Today that is
*harmless* — the worker runs one build at a time and no LLM call is live. It becomes the
first production incident the moment either changes.

**Landed (2026-08-14):**
- **Worker:** kept one build per process, as recommended — not rewritten.
- **Per-container resource limits** `[VERIFIED]`: `docker-compose.yml` now sets
  `mem_limit`/`cpus` on `postgres` (1g/1cpu), `worker` (3g/2cpu — the memory-risk service),
  and `api` (512m/1cpu). Dev-sized defaults with a comment pointing at where to raise them
  per host; not load-tested against these exact ceilings (see U9).
- **429-aware retry with `Retry-After`** `[VERIFIED]`: `image_gen_port.py`'s `_call` had a
  worse bug than blind backoff — `if exc.code < 500: raise` meant a 429 was **never
  retried at all** (429 < 500). Now honors `Retry-After` (seconds form, capped at 60s) and
  falls back to the existing exponential backoff only if the header is absent/unparseable.
  Covered by `services/cover/tests/test_image_gen_port.py::TestOpenRouterRateLimitRetry`
  (3 tests, passing).
- **Admission control** `[VERIFIED]`: `POST /v1/builds` now counts a tenant's
  `queued`+`running` rows and refuses with 429 at
  `PUBLISHER_MAX_QUEUED_BUILDS_PER_TENANT` (default 50). Covered by
  `tests/integration/test_bounded_concurrency.py` (written; not run this pass — no local
  Postgres/Docker available in the environment that authored it, see §5).

**Still deferred, deliberately:**
- **Bounded work pool sized from `policy.yaml`'s rate limits.** Not built. The only real
  inference call site (`OpenRouterImageGenAdapter.generate`) is called synchronously, one
  at a time, from inside a single stage — there is no concurrent dispatch to bound yet, and
  it stays gated behind `PUBLISHER_ALLOW_SIMULATED_INFERENCE` (U4). Building a semaphore
  around a call pattern that cannot yet be concurrent is exactly the speculative generality
  §2's anti-doctrine rules out. Revisit when a second concurrent call site exists — not
  before.

---

### U9 — Prove it under load
**Band gate:** 9.0 — this is what separates asserted from demonstrated

`FOR UPDATE SKIP LOCKED` is correct-by-construction and has, as far as the code and docs
show, **never been run with more than one worker** `[HYPOTHESIS — no multi-worker test
exists in `tests/`]`. The audit could only credit it as a correct-looking mechanism.

**Landed (2026-08-14), written but NOT executed this pass — no local Postgres/Docker
available in the authoring environment; run before trusting the checkmark:**
- `tests/integration/test_bounded_concurrency.py::test_three_workers_drain_queue_without_starvation`
  — 3 real `worker.py` subprocesses (not `docker compose --scale`, but the same claim
  mechanism against the same DB — the thing under test), 12 queued builds, asserts every
  build reaches a terminal state and no `build_stages` row was written more than once per
  `(build_id, stage_name)`.
- `tests/integration/test_bounded_concurrency.py::test_multi_worker_reclaim_has_no_double_processing`
  — the chaos case: a build stuck at `running` with a pre-expired lease (simulated dead
  worker), 3 live workers racing for it, asserts exactly one reclaim (`attempt` goes from
  1 to 2, not higher) and a live `worker_id` on the winner.

**Still open:**
- Scaled to 50 builds / `docker compose --scale worker=3` specifically, rather than 12
  builds / subprocesses — the mechanism is identical (same claim query, same DB), but the
  plan's literal numbers are untested.
- SSE load test: 50 concurrent clients against the N3 LISTEN/NOTIFY fix, assert pool
  exhaustion does not occur and p95 stays flat. Not built.
- `docs/PRODUCTION_READINESS.md` §3's chaos line bundles four scenarios ("kill a worker
  mid-build, kill the DB primary, fill the disk, saturate the pool"); only the first is
  covered by the tests above. Left unchecked rather than partially ticked — checking a
  bundled box for 1-of-4 would misrepresent it.

---

## 4. Sequencing

Dependency-ordered. U1 and U2 are independent and both block the 8.0 band, so they can run
in parallel; everything in stage 2 assumes both have landed.

```
Stage 1 (blocks 8.0)   U1 worker durability ─┐
                       U2 real manuscripts ──┼─► U3 declared deps ─► U4 AI quarantine
                                             │
Stage 2 (blocks 8.5)   U5 API hardening ─────┴─► U6 boundary hygiene ─► U7 observability
Stage 3 (blocks 9.0)   U8 bounded concurrency ─► U9 load proof
```

**Do U2 first if you can only do one thing.** U1 fixes builds that get stuck; U2 fixes
builds that succeed with the wrong book — and a confident wrong answer is worse than a
visible failure. U1 is the audit's CRITICAL, U2 is the one the audit missed.

Within Stage 1, U3 is ~15 minutes and can be slotted anywhere. U4 Phase A is mostly
deletion and renaming — schedule it when someone wants a low-risk PR.

---

## 5. Verification gates

Each workstream lands with a test that **fails against today's code**. This is the
non-negotiable part: a remediation without a failing-first test is an assertion.

| WS | Gate | Fails today? |
|---|---|---|
| U1 | Worker `SIGKILL` mid-build → second worker reclaims → `completed` | ✅ hangs at `running` forever |
| U1 | Stage raises `ValueError` → `status='failed'`, process exits non-zero | ✅ status stays `running` |
| U2 | Two tenants, two manuscripts → two different PDFs | ✅ byte-identical today |
| U2 | Build with no stored source → `BAD_INPUT`, never a fixture | ✅ silently builds the fixture |
| U3 | `lint_service_deps.py` in CI | ✅ flags `structure_wrangler.py:85` |
| U4 | `InferenceGateway` without explicit `simulate=True` → refuses to construct | ✅ constructs and fabricates |
| U5 | Concurrent identical `POST /v1/builds` → exactly one build | ✅ creates two |
| U5 | `casPath('../../etc/passwd')` → rejected | ✅ path-joins happily |
| U6 | `check_integrity()` clean with zero warnings | ✅ 4 orphans + 1 non-schema input |
| U7 | `/v1/health` with Postgres down → non-200 | ✅ returns `ok` |
| U8 | `POST /v1/builds` at tenant capacity → 429 | ✅ inserted unconditionally |
| U8 | OpenRouter 429 response → retried, not raised | ✅ raised immediately (`code < 500`) |
| U9 | 3 workers × 12 builds → each reaches a terminal state, no duplicate `build_stages` rows | written, not executed this pass (no local Postgres) |
| U9 | Expired-lease build + 3 live workers → reclaimed exactly once | written, not executed this pass (no local Postgres) |

Existing invariants that must not regress: 384 tests collected / 5 skipped / 0 failures via
`./scripts/test.ps1` (**never bare `pytest`**), `platform/stages/integrity.py` clean,
`lint_stage_versions.py` clean, and every stage whose behaviour changes gets a
`@stage(version=...)` bump.

---

## 6. Explicit non-goals

Naming these prevents scope creep from eating the plan:

- **No Temporal migration.** A5, sanctioned deferral (`ARCHITECTURE.md:199`).
- **No Typst or IDML emitter.** P6/P9 product scope, not architecture remediation.
- **No async rewrite** of the pipeline. See U8 for why one-build-per-process is correct.
- **No microservice split.** The hidden-monolith finding is the right design at this scale;
  the fix is honest naming, not new containers.
- **No dependency-injection framework, no repository layer for the Python core.**
- **No ADR backfill.** A5 defers the ADR log; this document plus the existing remediation
  docs carry decision history adequately for now.

---

## 7. Expected outcome

| Dimension | Now | After Stage 1 | After Stage 2 | After Stage 3 |
|---|---|---|---|---|
| Score | 6.0 | ~8.0 | ~8.6 | ~9.0 |
| CRITICAL | 2 *(incl. N1)* | 0 | 0 | 0 |
| HIGH | 3 | 0 | 0 | 0 |
| MEDIUM | 6 | 6 | ≤1 | ≤1 |
| Maturity | Early Production | Early Production | Production-capable | Production |

Stage 1 alone converts the system from *"reports success it has not earned"* to *"fails
honestly and builds the right book"* — which is the difference that matters to a customer,
and the same standard the two hard gates were built to enforce. Stages 2 and 3 make it
operable and prove it scales.

The pipeline core — derived DAG, fixpoint reachability, content-addressed storage, the two
hard gates — needs **no structural change** in this entire plan. It is the strongest part
of the system and every workstream above is written to protect it rather than touch it.
