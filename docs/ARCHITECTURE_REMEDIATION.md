# Publisher — Architecture Remediation Plan

Fix plan for the findings in the ARCH-AUDIT-V2 pass (2026-08-01). Scored **5/10, maturity Prototype, urgency Immediate**.

Companions: [ARCHITECTURE.md](ARCHITECTURE.md) · [BUILD_PLAN.md](BUILD_PLAN.md) · [REMEDIATION_PLAN.md](REMEDIATION_PLAN.md) (completed — correctness/gate scope) · [PARALLELIZATION.md](PARALLELIZATION.md) · [OPTIMIZATION.md](OPTIMIZATION.md).

**Relationship to the completed [REMEDIATION_PLAN.md](REMEDIATION_PLAN.md).** That plan closed the *correctness* gaps — gates that could not fail, a DAG that bypassed them, missing enforcement. It succeeded, and its §F5 explicitly deferred four items as out of scope. **This plan picks up two of those four** (`CacheStore` backend, real execution wiring) because the audit found they are not independent deferrals — they are the same missing subsystem, and their absence now invalidates load-bearing claims in the architecture docs. The other two F5 items (real Ghostscript, real Typst emitter) remain correctly deferred and are **not** in scope here.

---

## 1. The shape of the problem

### 1.1 Two CRITICAL findings, one root cause

The audit reported two CRITICAL items on apparently unrelated axes:

- `packages/api` has **zero** integration with the Python pipeline. Every route is backed by in-process `Map()`s and hardcoded literals. `[VERIFIED]`
- `platform/cache` has **no storage backend at all** — 145 lines of pure key-derivation functions, zero I/O. `[VERIFIED]`

They are the same bug. Both exist because **no state in this system outlives a single `DagExecutor.execute()` call.**

- The cache cannot cache because [tracer_bullet.py](../tracer_bullet.py) roots the CAS inside `tempfile.TemporaryDirectory(...)`, deleted on `with` exit. There is nothing for a cache index to point *at* after a build ends, so nobody built one. `[VERIFIED]`
- The API cannot integrate because there is no durable build record to reference across a process boundary, no artifact that survives to be downloaded, and no queue to enqueue onto. So the routes fabricate. `[VERIFIED]`

**Therefore the fix is not two fixes.** Build the missing execution-tier boundary — durable state plus a process boundary — once, and both CRITICALs close as consequences. Sequencing them as independent workstreams would mean building the persistence layer twice.

### 1.2 What the audit found is *not* broken

Scope discipline depends on being explicit about this. The following are sound and must not be "improved" during this work:

- The declarative stage registry and derived DAG (`platform/stages`) — matches [BUILD_PLAN §3.20](BUILD_PLAN.md) closely. `[VERIFIED]`
- Dependency direction: `stages → services → platform`, no cycles, no layer leaks. `[VERIFIED]`
- LLM provider isolation: `platform/routing/policy.yaml` is genuinely policy-as-data, with `allow_fallbacks: false` discipline and Anthropic-vs-OpenRouter parameter separation correctly documented and separated. `[VERIFIED]`
- The Fastify server's *security* layer — auth hook, tenant isolation with 404-not-403, idempotency replay, allow-list CORS, rate limiting. It is well built. It is simply wired to nothing. `[VERIFIED]`
- Agent tool-surface scoping (`ToolRegistry.allowed_for`) and the zero-AST-write guarantee. `[VERIFIED]`

### 1.3 Findings → fixes

| # | Finding | Sev | Fix |
|---|---|---|---|
| 1 | `packages/api` fabricates every build response; zero pipeline integration | **CRITICAL** | A2 |
| 2 | `platform/cache` has no storage backend; no build can ever hit cache | **CRITICAL** | A1.2 |
| 3 | CAS root is a per-build temp dir; nothing survives a build | **CRITICAL** (enabler of 1 and 2) | A1.1 |
| 4 | Sequential executor, no fan-out, no admission control despite declared budgets | HIGH | A1.4, A3.3 |
| 5 | Module-level `Map()` state in `index.ts` — not restart- or scale-safe | HIGH | A2.1 |
| 6 | No concurrency bound on agent LLM calls; `execute()` is sync | HIGH | A3.1, A3.2 |
| 7 | `adapters.py`/`billing.py` are Python inside a TS package, orphaned from the live path | HIGH | A4.1 |
| 8 | No deployment manifests anywhere (no Dockerfile/compose/k8s) | HIGH | A1.5 |
| 9 | Domain logic runs on untyped `dict`s despite a full codegen pipeline | MEDIUM | A4.3 (opportunistic) |
| 10 | `paginate_stage` imports private `_ast_to_html`/`_emit_css` from sibling stages | MEDIUM | A4.2 |
| 11 | Sandbox tiers declared but unenforceable without containers | MEDIUM | resolved by A1.5 |
| 12 | God modules (`publisher_stages/__init__.py` 491L, `rules.py` 748L) | LOW–MED | **A5 — deferred** |
| 13 | No ADR log | LOW | A5 — deferred |
| 14 | `cli.py schema validate` prints "Valid JSON" without validating against any schema | MEDIUM | A0.3 |

Finding 14 was found while grounding this plan, not in the audit pass. It is the same failure family as finding 1 — a command that reports success without doing the work it names.

---

## 2. Method

The completed [REMEDIATION_PLAN.md](REMEDIATION_PLAN.md) used *detectors-first, in the red*, and that discipline is why it converged. Same method here, with one addition specific to architecture work:

**An architectural defect's detector is an integration test that crosses the seam.** Unit tests cannot catch "these two subsystems have never met." The two detectors in A0 are currently *impossible to write* — they will not compile against today's code — and that impossibility is precisely the finding.

**Working rule: land each detector in the same PR as its fix, detector first in the diff.**

One additional rule for this plan specifically:

**Resolve the audit's `[UNKNOWN]`s before designing against them (A0.1).** The audit honestly labeled three items as untraced. Planning around an assumption in either direction would be fabrication. They are cheap to settle and could change A3's shape.

---

## A0 — Resolve unknowns, write detectors (2 days)

### A0.1 — Settle the three `[UNKNOWN]`s

| Question | Why it changes the plan | Where to look |
|---|---|---|
| Does `RouteConfig.fallback_route` actually **execute** on failure, or is the field merely carried? | If it's dead, cascade routing (a documented cost control, [LLM_STRATEGY.md](LLM_STRATEGY.md) §4) does not exist, and A3 gains an item | `services/structure/publisher_structure/inference.py`, call sites of `fallback_route` |
| Is agent tool output **validated against `schemas/agent-proposal/`** before entering the override log? | If not, the closed-union `value` field fixed in the prior remediation is enforced only on paper | `services/agents/publisher_agents/runtime.py`, `propose_override` consumers |
| Does `AgentRuntime` read/write through `services/learning`, or bypass it? | Determines whether L1/L2/L3 memory scoping ([BUILD_PLAN §3.19](BUILD_PLAN.md)) is real or aspirational | `services/learning/publisher_learning/__init__.py` (548 L, untraced), imports into `services/agents` |

Output: a short findings note appended to this file. Each answer is `[VERIFIED]` or the item escalates into A3.

### A0.2 — The two detectors (must fail on landing)

```python
# tests/integration/test_api_drives_pipeline.py
def test_build_request_produces_a_real_artifact():
    """
    The seam. POST a build, poll to completion, download the artifact, and assert
    the bytes are what the pipeline actually produced -- not a fabricated URL.

    Currently impossible: POST /v1/builds writes to an in-process Map and
    GET /v1/builds/:id returns a hardcoded 7-stage literal with durationMs
    120/340/890/45/4300/2100/30 regardless of what (if anything) ran.
    """

def test_second_build_of_same_input_hits_cache():
    """
    Run the same manuscript+design twice. The second run's stage results must
    report cache_hit=True for unchanged stages.

    Currently impossible: the CAS root is a TemporaryDirectory scoped to one
    execute() call, so run two shares no bytes with run one, and no cache index
    exists to record the relationship.
    """
```

### A0.3 — Anti-fabrication regression guard *(finding 1, 14)*

A behavioural test asserting API responses derive from stored state, not literals:

```python
def test_build_status_is_not_a_hardcoded_literal():
    """A build that never ran must not report seven completed stages."""
    # create build, do NOT run a worker, assert status == "queued"
    # and stages == [] -- today it returns the full fabricated completion payload
```

Plus: make `pub schema validate` actually validate against the named schema (compiled validator per [BUILD_PLAN §3.1](BUILD_PLAN.md)) or fail. A command that prints "Valid JSON" after `json.loads` succeeds is reporting the wrong thing under a name that implies schema conformance.

**Acceptance.** All three detectors present, all three failing, CI red.

---

## A1 — Durable state and the execution boundary (5 days)

The heart. Closes findings 2, 3, and enables 1.

### A1.1 — Durable CAS root *(finding 3)*

**Root cause.** [tracer_bullet.py](../tracer_bullet.py): `with tempfile.TemporaryDirectory(...) as work_dir: cas_root = work_dir_path / ".cas"`.

**This is a small change, and the code already supports it.** [cli.py](../cli.py) constructs `ContentAddressedStore(CasConfig(local_cache_root=Path(".cas-cache")))` against a durable path today, and the executor already builds sharded paths (`cas_root / h[:2] / h[2:4] / h`). `[VERIFIED]` The CAS was always capable of persistence; only the executor's root was ephemeral.

**Change.** CAS root from config (`PUBLISHER_CAS_ROOT`, default `./.publisher/cas`), not from a temp dir. The per-build temp dir remains for genuinely scratch work, but **no artifact registered in a `StageResult` may live there.**

### A1.2 — `CacheStore` *(finding 2)*

**Root cause.** `platform/cache` has `compute_cache_key()` and `compute_toolchain_digest()` — both correct, both already handling the subtle cases (canonical JSON, float normalisation, refusing to stringify un-encodable objects rather than embedding a memory address). What is missing is *storage*. `[VERIFIED]`

**This is wiring plus a table, not new design.** The executor already tracks input artifact hashes (`artifact_paths`), and every stage already declares `version`; the cache key function's inputs are all present at the call site today.

```sql
CREATE TABLE cache_index (
  cache_key    TEXT PRIMARY KEY,
  stage        TEXT NOT NULL,
  version      INTEGER NOT NULL,
  output_refs  JSONB NOT NULL,   -- artifact_kind -> sha256
  created_at   TIMESTAMPTZ NOT NULL,
  hit_count    INTEGER NOT NULL DEFAULT 0,
  last_hit_at  TIMESTAMPTZ
);
```

Executor integration, per stage: compute key → `SELECT` → on hit, skip execution and reuse `output_refs`; on miss, execute and `INSERT`. Cache hit/miss lands in `build_stages.cache_hit` so A0.2's second detector can assert on it.

**Correctness guard, non-negotiable.** A wrong cache key serves wrong output silently — worse than no cache. [BUILD_PLAN §3.3](BUILD_PLAN.md) requires a nightly cold-vs-cached byte-equality job; it must ship **with** this, not after. The stage-version CI rule already exists from the prior remediation.

### A1.3 — Build state schema *(enables findings 1, 5)*

```sql
CREATE TABLE builds (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, document_id TEXT NOT NULL,
  design_id TEXT, profile_ids JSONB, mode TEXT NOT NULL DEFAULT 'proof',
  status TEXT NOT NULL,           -- queued|running|completed|failed
  error_kind TEXT, error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ
);
CREATE TABLE build_stages (
  build_id TEXT NOT NULL REFERENCES builds(id), stage_name TEXT NOT NULL,
  stage_version INTEGER NOT NULL, status TEXT NOT NULL,
  cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
  duration_ms INTEGER, metrics JSONB,
  started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
  PRIMARY KEY (build_id, stage_name)
);
CREATE TABLE artifacts (
  build_id TEXT NOT NULL REFERENCES builds(id), kind TEXT NOT NULL,
  schema_id TEXT NOT NULL, sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
  size BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (build_id, kind)
);
```

`tenant_id` on `builds` is load-bearing: it is what lets the API's existing (and correct) `assertTenant` checks survive the move off in-memory Maps.

**Postgres, not SQLite.** Considered and rejected: SQLite avoids standing up a service, but (a) [BUILD_PLAN](BUILD_PLAN.md) names Postgres as the target throughout, so SQLite means writing the persistence layer twice; (b) the queue in A1.4 depends on `FOR UPDATE SKIP LOCKED`, which SQLite lacks; (c) a Node API and a Python worker sharing one SQLite file across processes is a known sharp edge. The cost of Postgres is a compose file — which finding 8 says we owe anyway. **Two findings, one fix.**

### A1.4 — Worker loop and DB-backed queue *(finding 4, partial)*

```sql
SELECT * FROM builds WHERE status='queued'
ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1;
```

`FOR UPDATE SKIP LOCKED` is what makes a table a correct multi-worker queue without double-claiming. Claim → `status='running'` → execute the DAG → write `build_stages` rows as each completes → write `artifacts` → `completed`/`failed` with the error taxonomy's `kind`.

**Admission control uses data that already exists.** Every stage declares `memory_budget_mb` and `queue`; [BUILD_PLAN §4.1](BUILD_PLAN.md) requires rejecting a job when projected RSS exceeds headroom. The worker sums in-flight budgets before claiming. No new declaration needed — only a consumer for the one already there.

**`tracer_bullet.py` is demoted, not deleted.** It becomes explicitly what it already is: a local dev harness. The worker owns production execution. Its `allow_stub_engines=True` must **not** propagate to the worker path — that flag exists precisely so real builds fail loudly on a missing engine.

### A1.5 — Deployment manifests *(finding 8, resolves 11)*

`docker-compose.yml`: `postgres`, `api` (Node), `worker` (Python). A Dockerfile per service. This is the first artifact in the repo that can run the system anywhere but a developer's machine.

Follow-on, free: the sandbox tiers in `platform/sandbox` become *enforceable* once there is a container boundary to enforce against — finding 11 closes without its own work item.

**Acceptance for A1.** A build enqueued by SQL and executed by the worker produces artifacts that exist after the process exits. Running the same input twice reports `cache_hit=True` on unchanged stages (A0.2 detector 2 goes green). `docker compose up` brings the system up from a clean checkout.

---

## A2 — API integration (3 days)

Closes findings 1 and 5. Depends entirely on A1.

### A2.1 — Replace in-memory stores *(finding 5)*

`titles`, `builds`, `manuscripts`, `idempotencyStore`, `webhooks` → Postgres. **Keep every existing tenant check exactly as written** — `assertTenant`, 404-not-403, the tenant-scoped idempotency key. That logic is correct; only its backing store changes.

### A2.2 — Wire the routes to real state *(finding 1)*

| Route | Today | After |
|---|---|---|
| `POST /v1/builds` | `Map.set`, returns fabricated id | `INSERT ... status='queued'`, returns real id the worker will claim |
| `GET /v1/builds/:id` | hardcoded 7-stage completion literal | `SELECT` build + `build_stages` |
| `GET /v1/builds/:id/events` | `setTimeout` loop over a fixed array | SSE over real `build_stages` transitions (poll, or `LISTEN/NOTIFY`) |
| `GET /v1/builds/:id/preflight` | hardcoded pass/warn payload | read the `preflight/1` artifact from CAS |
| `GET /v1/builds/:id/artifacts/:kind` | fabricated URL to a nonexistent host | `SELECT` from `artifacts`, serve/presign the real CAS blob |
| `GET /v1/manuscripts/:id/structure` | hardcoded 2-chapter list | read the `ast/1` artifact |

**Acceptance.** A0.2 detector 1 and A0.3 both go green. No route handler contains a literal payload representing build state.

---

## A3 — Concurrency and load safety (3 days)

### A3.1 — Bound concurrent LLM calls *(finding 6)*

**Root cause.** No semaphore anywhere in the agent or inference layers. The prior remediation added `max_turns`/`max_subagents` **budget** checks to `AgentRuntime.execute()`, but those cap one call's work — nothing prevents N simultaneous `execute()` calls from opening N simultaneous outbound requests. `[VERIFIED — absence]`

Semaphore keyed by (role, tenant), bound from routing policy. Not a global lock: one tenant's fan-out must not stall another's.

### A3.2 — Async the inference path *(finding 6)*

`AgentRuntime.execute()` is `def`, not `async def` ([runtime.py:237](../services/agents/publisher_agents/runtime.py), `[VERIFIED]`). Under a worker pool this blocks a worker per in-flight model call. Convert the call path; keep the pure scoring/validation core synchronous (it is deterministic and should stay so — D1).

### A3.3 — Per-profile fan-out *(finding 4, remainder)*

[BUILD_PLAN §3.13](BUILD_PLAN.md): "child workflows per output profile so one failing profile doesn't fail the build." Today one `for stage_name in order:` loop runs everything sequentially, so a 6×9 paperback failing preflight also kills the EPUB. Split per profile once the worker owns execution.

**Acceptance.** A load test at 10× current concurrency: no unbounded outbound request growth, admission control rejects rather than OOMs, one failing profile does not fail its siblings.

---

## A4 — Boundary hygiene (2 days)

### A4.1 — Relocate `adapters.py` / `billing.py` *(finding 7)*

Python modules living inside `packages/api/src` (a Node/TS package), imported only by `packages/api/tests/test_platform.py` and never by `index.ts`. `[VERIFIED]` One of them (`AdobeInDesignAdapter`) reaches across into `services/idml` — a domain import inside what should be a thin API layer.

Move to `services/` (e.g. `services/billing`, `services/rendering-adapters`). The worker calls them; the API calls the worker. **Note:** this breaks `packages/api/tests/test_platform.py`'s imports — small, expected, fix in the same PR.

### A4.2 — Promote the shared AST→HTML renderer *(finding 10)*

`paginate_stage` imports `stages.extract_stage._ast_to_html` and `stages.design_compile_stage._emit_css` — private, underscore-prefixed, cross-sibling. Deliberate (the prior remediation made `paginate` reuse *exactly* the renderer whose output the integrity gate validated — that identity is load-bearing), but a private-API coupling the stage registry is supposed to mediate.

Extract to a public shared module both stages import. **Constraint that must survive the refactor:** `extract` and `paginate` must call the *same* function. Two copies would silently break the guarantee that pagination renders what the integrity gate actually verified.

### A4.3 — Typed models at boundaries *(finding 9, opportunistic)*

Stage bodies use `json.loads(...)` + `.get(...)` chains despite `schemas/py/` and `schemas/ts/` carrying generated types. Full conversion is a large diff with low immediate payoff. **Do it only where a stage is already being modified for A1–A3**, at input parse and output construction. Do not open a dedicated conversion PR.

---

## A5 — Explicitly deferred

Not defects to fix now. Listed so they are not mistaken for oversights.

| Item | Why deferred | Revisit when |
|---|---|---|
| God modules (`publisher_stages/__init__.py`, `rules.py`) | LOW–MED, no user impact, touches everything A1–A3 also touches | After A1–A3 land, as a standalone refactor |
| Full dict→typed-model conversion | Large diff, low payoff, correctness risk without commensurate gain | Opportunistically (A4.3) |
| ADR log | Real gap, but `docs/` already carries the decisions in prose | Next architecture decision worth recording |
| Temporal migration | [BUILD_PLAN](BUILD_PLAN.md) puts it at P5; DB-as-queue is the documented P0/P1 shape and A1.4 delivers exactly that | Queue depth or workflow complexity outgrows SQL |
| Real Ghostscript, real Typst emitter | Correctly deferred by [REMEDIATION_PLAN §F5](REMEDIATION_PLAN.md); stubs now refuse rather than certify | P1 / the O1 renderer gate |

---

## 3. Sequencing and effort

```
A0  unknowns + detectors (red)     2 days  ── makes every claim below verifiable
A1  durable state + boundary       5 days  ── closes both CRITICALs
A2  API integration                3 days  ── depends entirely on A1
A3  concurrency + load safety      3 days  ── independent of A2
A4  boundary hygiene               2 days  ── independent
                                  ─────────
                                  ~15 days (1 engineer, ~3 weeks)
```

**Why this order.** A0 first because two of the three detectors are currently *impossible to write*, and that impossibility is the finding — writing them defines "done" precisely. A1 before A2 because the API has nothing to integrate against until durable state exists; inverting them means mocking the thing you are about to build. A3 and A4 are independent of A2 and parallelize with two engineers (~10 days wall-clock).

**Critical path:** A0 → A1 → A2. A3/A4 hang off A1 only.

---

## 4. Non-goals

- **No rewrite of the Python pipeline.** The stage registry, DAG derivation, dependency direction, and routing policy are sound (§1.2). This plan adds a persistence layer and a process boundary around them.
- **No new architecture.** Every element here — Postgres DAG executor, worker pool, admission control from declared budgets, per-profile children — is already specified in [BUILD_PLAN.md](BUILD_PLAN.md)/[ARCHITECTURE.md](ARCHITECTURE.md). The gap is implementation, not design. Do not redesign en route.
- **No premature Temporal.** A1.4 delivers the documented intermediate shape.
- **No API rewrite.** The Fastify server's auth/tenancy/idempotency layer is good work. Change its backing store, not its security model.

---

## 5. Risk

| Risk | Mitigation |
|---|---|
| **Wrong cache key serves wrong output silently** — worse than no cache | Nightly cold-vs-cached byte-equality job ships *with* A1.2, not after. Stage-version CI rule already exists. |
| **The integration test surfaces bugs invisible today** | Expected, not a regression. These two subsystems have never run against each other; first contact *will* find defects. Budget discovery time in A2 rather than treating it as scope creep. |
| Postgres is a new operational dependency | It is the documented target, and A1.5 makes it one `docker compose up`. The alternative writes the persistence layer twice. |
| A1.1 durable CAS grows unbounded | GC is already specified ([BUILD_PLAN §3.3](BUILD_PLAN.md)); a retention job is required before this runs anywhere real, not before it runs locally. |
| A4.2 refactor accidentally forks the renderer | Assert in test that `extract` and `paginate` reference the same function object. The integrity guarantee depends on identity, not equivalence. |
| Moving `adapters.py`/`billing.py` breaks existing tests | Known, small, same PR. |

---

## 6. Definition of done

- [ ] All three A0.1 unknowns answered and labelled `[VERIFIED]`
- [ ] `POST /v1/builds` → poll → download produces bytes the pipeline actually generated
- [ ] No API route handler contains a literal payload representing build state
- [ ] A build's artifacts exist after the executing process exits
- [ ] Running the same input twice reports `cache_hit=True` on unchanged stages
- [ ] Nightly cold-vs-cached byte-equality job green
- [ ] A build that never ran reports `queued` with zero stages — not seven completed ones
- [ ] `pub schema validate` validates against a schema or fails
- [ ] `docker compose up` brings up postgres + api + worker from a clean checkout
- [ ] Concurrent LLM calls bounded per (role, tenant); `execute()` async
- [ ] One failing output profile does not fail its siblings
- [ ] No Python modules under `packages/api/src`
- [ ] `extract` and `paginate` provably share one renderer function
- [ ] Admission control rejects over-budget builds instead of OOM-ing

---

## 7. The one-line summary

The pipeline and the API are two well-built systems that have never met, and the cache cannot cache because nothing outlives one build. **All three are the same missing thing — durable state and a process boundary — and the highest-value work in this plan is building it once rather than three times.**

---

## A0.1 findings note (2026-08-05)

All three resolved as findings of absence — each was a real gap, not a false alarm.

| Question | Answer | Evidence |
|---|---|---|
| Does `fallback_route` execute? | **No — dead field.** `[VERIFIED]` | [inference.py:84](../services/structure/publisher_structure/inference.py) declares it, [inference.py:228](../services/structure/publisher_structure/inference.py) parses it from YAML into `RouteConfig`. Grep for `.fallback_route` across the repo returns only those two lines — nothing ever reads it back. Cascade routing as a cost control ([LLM_STRATEGY.md](LLM_STRATEGY.md) §4) does not exist at runtime; it exists only as config that is silently ignored. |
| Is agent tool output validated against `schemas/agent-proposal/` before entering the override log? | **No — tagged, not validated.** `[VERIFIED]` | [runtime.py:353](../services/agents/publisher_agents/runtime.py) and [runtime.py:382](../services/agents/publisher_agents/runtime.py) attach the literal string `"schema": "agent-proposal/1"` to the output dict. Grep for `jsonschema`/`validate` in `runtime.py` and in `overrides.py` (the consumer) returns nothing. The closed-union `value` field fixed in the prior remediation is enforced only where Python's own type system happens to catch it — not by schema conformance. |
| Does `AgentRuntime` read/write through `services/learning`? | **No — full bypass.** `[VERIFIED]` | Grep for `learning` (case-insensitive) anywhere under `services/agents` returns zero matches. `services/learning/publisher_learning/__init__.py` is never imported by the agent runtime. L1/L2/L3 memory scoping ([BUILD_PLAN §3.19](BUILD_PLAN.md)) is aspirational — the module exists and is tested in isolation, but nothing calls it. |

**Effect on A3.** All three escalate per the plan's own rule. A3 gains two items not costed in §3's original 3-day estimate:

- **A3.4 — Wire or delete `fallback_route`.** Either make `RouteConfig` execution actually retry against `fallback_route` on the primary's failure (closing the documented cost-control gap), or remove the field and the doc claim. Leaving it half-declared is its own small instance of finding 1's pattern (the config says one thing, the runtime does another).
- **A3.5 — Validate agent output against `schemas/agent-proposal/agent-proposal.schema.json` before it reaches `overrides.py`.** A compiled-validator check at the point proposals leave `AgentRuntime`, same shape as A0.3's schema-validate fix for `cli.py` — reject rather than tag-and-trust.

`services/learning` bypass is **not** added as an A3 item — wiring memory scoping into the agent loop is new behavior, not a fix to broken behavior, and is out of scope for a remediation plan. Flagged here so it is not mistaken for an oversight; revisit as a feature, not a defect.

---

## A1/A2 verification note (2026-08-05)

A2 was implemented and verified end-to-end against a real `docker compose up` stack (Postgres + containerized worker + containerized API on the internal docker network — see below for why *internal* network, not host-published ports, was required for Postgres specifically).

**Confirmed working, by direct HTTP exercise of the real stack:**
- `POST /v1/builds` → real row, `status: 'queued'`. `GET /v1/builds/:id` immediately after → `status: 'queued'`, `stages: []`. **A0.3's detector passes**: nothing is fabricated.
- The worker claims the queued build (`FOR UPDATE SKIP LOCKED`), transitions it to `running`, and the DAG genuinely executes — `acquire` → `extract` → `ast-assemble` → `design-compile` → `resolve` → `paginate` all completed for real, including a real weasyprint PDF render (after an unrelated `pydyf` pin fix, see below).
- `test_second_build_of_same_input_hits_cache` (A1.2) passes: second identical run reports `cache_hit=True`.

**A0.2 detector 1 (`test_build_request_produces_a_real_artifact`) is still red — for a reason A2 cannot fix.** The DAG reaches `finish` (v3) and fails there: `finish_stage.py` unconditionally requires `allow_stub_engines=True` or raises `ErrorKind.INFRA` — "Ghostscript PDF/X conversion is not wired yet." `worker.py` deliberately runs with `allow_stub_engines=False` (A1.4: "that flag exists precisely so real builds fail loudly on a missing engine"). **This means no build can ever reach `completed` with a real artifact until real Ghostscript integration exists** — which `REMEDIATION_PLAN.md §F5` and this plan's own §A5 both explicitly, deliberately defer to "P1 / the O1 renderer gate." A2's route-wiring is not the blocker; the renderer gap A5 already named is. Detector 1 was written before this dependency was traced through — it was implicitly assuming a capability neither plan ever promised to deliver at this phase.

**Two real bugs found and fixed along the way, both worth keeping independent of A2:**
- `pydyf>=0.11` removed the `Stream.transform` override signature weasyprint 62.x calls — `AttributeError: 'super' object has no attribute 'transform'` on every real render. Pinned `pydyf<0.11` in `requirements.txt`. Without this pin, weasyprint *imports* cleanly but fails at the first actual `write_pdf()` call — the kind of failure that only shows up under real use, not a smoke-test import check.
- `Dockerfile.worker`'s apt package list used `libgdk-pixbuf2.0-0`, renamed upstream on current Debian to `libgdk-pixbuf-2.0-0` — the build never completed until this was fixed.

**Environment note, not a code defect:** on this Windows + Docker Desktop host, TCP connections from the host to the Postgres container's *published* port silently fail Postgres's password authentication (`28P01`) — reproduced identically via `psql`, `psycopg2`, and Node's `pg`, while the exact same credentials succeed from any container on the compose network, and from a container using the internal docker-network hostname. HTTP through the same published-port mechanism (the API's port 4000) works fine, isolating this to Postgres's wire protocol specifically, not Docker Desktop's port-forwarding in general. Root cause not fully identified (a Docker Desktop / WSL2 NAT quirk is the leading suspect); worked around by running the API and worker as containers on the compose network rather than as host processes reaching for a host-forwarded Postgres port. Consequence: `tests/integration/test_api_drives_pipeline.py`'s own fixtures (which spawn `tsx`/`worker.py` as bare host processes against `DATABASE_URL=localhost:5432`) cannot run automatically on this machine as currently written — the routes were verified by direct HTTP exercise of the containerized stack instead. Fixing the automated fixture to work around this (e.g., by also running the test's own driver code in-network, or resolving the underlying Docker Desktop issue) is unresolved and worth a follow-up if this environment is the CI target.
