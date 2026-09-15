# Architecture Score Plan — 5/10 → 10/10

**Source audit:** EGFV v3.0, 2026-09-09 — `5 / 10` (SoC 1, Consistency 1, Coupling 1,
Testability 1, Resilience 1). Twenty root causes, `L1`–`L20`. One CRITICAL (`L5`),
four HIGH (`L1`–`L4`), three `[FALSE]` docs-drift rows (`L18`–`L20`).

**Target:** `> 9 / 10`. On a five-dimension 0–2 rubric that means **10/10** — every
dimension at 2. There is no 9.

**Item prefix:** `E` (EGFV). Cite as `E3.2` the way the repo cites `D8`, `A3`, `U5`, `G2`.

**Relationship to existing plans.** This supersedes nothing. It *closes out* the open
tails of three drafts and states the dependency:

| Existing | Status | This plan |
|---|---|---|
| `VERIFICATION_PLAN.md` G1–G8 | draft, nothing implemented | **E0 implements it** and generalises it into one meta-gate |
| `ARCHITECTURE_REMEDIATION.md` A3–A5 | open | A3.1/A3.2 folded into **E6**; A4.1/A4.2 into **E1**; A5 re-examined in **E7** |
| `ARCHITECTURE_UPLIFT_PLAN.md` U8–U9 | U8 open, U9 written-not-executed | **E0.3** executes U9; U8 admission control becomes a middleware (**E2.3**) |

---

## 0. How the score actually moves

Bands gate; points do not accumulate. But the audit's rubric is *per dimension*, so the
useful table is per dimension, not per band.

| Dimension | Now | What pins it at 1 | What 2 requires | Workstreams |
|---|---|---|---|---|
| Separation of concerns | 1 | `L4` executor at repo root · `L6` three platform subsystems wired to nothing · `L9` three dead services · `L12` auth decision split across a hook and every route body | One home per concern; every shipped package on an executing path or explicitly quarantined out of the image | E1, E3, E5, E7 |
| Consistency with baseline | 1 | `L18` `L19` `L20` baseline describes a system that was not built · `L7` a flag named `simulate` that does not select simulation · `L1` a gate declared blocking that cannot fail | Zero `[FALSE]` rows, and a CI gate that keeps it zero | E0, E6, E7 |
| Coupling & dependency health | 1 | `L4` production imports from the dev harness · `L15` module-global registry mutated at import time from an env var · `L11` a field whose name contradicts its value | Graph identity is a function of explicit config, not import order; no cross-tier import; enforcement is declarative | E1, E2 |
| Testability & observability | 1 | `L2` no Postgres in CI ⇒ 24 integration tests skip · `L3` 3.3k LOC of TypeScript, zero tests, never type-checked · `L16` 553 s suite claiming 13.8 s | Every declared gate executes, is proven able to fail, and the suite is fast enough to run | E0 |
| Failure resilience & scalability | 1 | `L5` root + writable rootfs + open egress parsing hostile input · `L10` deadlines and memory budgets declared and enforced nowhere · `L13` tenancy by convention · `L14` no migration path | Budgets enforced by the runtime; isolation enforced by the database; scale demonstrated, not asserted | E2, E3, E4 |

### 0.1 The ceiling problem — read this before costing anything

`ARCHITECTURE_UPLIFT_PLAN.md §0` already says it: *"10/10 is not reachable while
`docs/ARCHITECTURE.md` describes a 21-stage pipeline with Temporal orchestration, three
DesignSpec emitters and a cross-emitter agreement gate, of which one emitter exists."*

That is correct, and it is the same fact as `L18`/`L19`. **The Consistency dimension is
measured against `ARCHITECTURE.md`, so the score cannot exceed what that document makes
achievable.** There are exactly two ways out:

- **(a) Build the described system.** Three emitters, a cross-emitter agreement gate,
  S3/R2 object storage, `packages/orchestrator`, `packages/worker-render`,
  `services/design`, `infra/`. Months of work, most of it not currently justified by a
  user need.
- **(b) Make the normative document describe the system that exists, and move the
  unbuilt parts to a dated roadmap document that is explicitly *not* the baseline.**

**This plan takes (b), and says plainly that it is partly a documentation fix.** That is
legitimate — a baseline document's job is to state the rules the code must hold to, and a
baseline describing a different system cannot do that job — but it is only legitimate if
the aspirations stay visible. So `E7.1` does not delete them; it moves them to
`ARCHITECTURE_ROADMAP.md` with dates and owners, and `E7.3` adds a CI gate that keeps
`ARCHITECTURE.md` true from then on. Without `E7.3` this is score-gaming. With it, it is
the only durable fix, because the failure mode being closed is *documents drifting from
code silently*, and only a gate closes that.

If the user prefers **(a)**, stop here: this plan does not cost it, and the realistic
target under (a) is 8–8.5 within a quarter, not 10.

---

## 1. Paradigm doctrine — the target shape per module

The request was the *optimal* paradigm and patterns per module. For the core of this
repo the honest answer is still the one `ARCHITECTURE_UPLIFT_PLAN.md §2` gave: **it is
already right, and the value is in naming why so future work does not erode it.** What
follows changes only where the audit found a defect, and says "keep" loudly everywhere
else.

| Module | Target paradigm | Patterns | Change |
|---|---|---|---|
| `platform/stages` (registry, DAG, integrity) | **Declarative data + total functions over it.** Stages are data; the graph is a pure function of that data | Registry · Declaration-as-data · Derived graph · Fixpoint reachability · **Builder → immutable snapshot** | ⚠️ **E1.2.** `derive_dag()` and `check_integrity()` stay untouched — they are the best-engineered code in the repo. The defect is *lifecycle*: a module-global mutated at import time. Replace with `build_registry(config) -> FrozenRegistry` |
| `platform/exec` *(new)* | **Functional core, imperative shell.** `plan()` is pure; `run()` is the only effectful half | Interpreter-over-plan · Middleware chain · Strategy (sandbox tier) | ❌ **E1.1 + E2.** New package. `DagExecutor` moves here out of `tracer_bullet.py` |
| `platform/sandbox` | **Object capability.** A stage receives exactly an ro input dir, a rw output dir, and a budget — nothing else | **Port (`typing.Protocol`)** · Adapter per tier · Null Object (dev tier, named so it cannot be mistaken for real) | ❌ **E3.2.** The types already exist and express the right model. They have no call site. Give them a Port and call it from the middleware |
| `platform/cas`, `platform/cache` | **Immutable content-addressed store.** The hash *is* the identity | Value Object · Cache-aside · Pluggable store | ✅ Keep unchanged |
| `platform/db` | **Ordered immutable migrations + database-enforced tenancy** | Migration ledger (`schema_migrations`) · **Row-Level Security with a session GUC** · least-privilege roles | ❌ **E4.** Today: `CREATE TABLE IF NOT EXISTS` in `initdb`, tenancy by convention |
| `stages/*.py` | **Imperative shell over a functional core** (D1) | Command · Ports & Adapters, constructor-injected | ✅ Keep the shape. **E1.2** removes the import-time env read and the `select_implementation` mutation from `__init__.py` |
| `services/*` (ingest, prepress, structure/rules, structure/overrides, agents) | **Functional core** — pure functions over immutable data, no I/O, no platform imports | Hexagonal with ports defined by the **consumer** · frozen dataclasses | ✅ Keep. This boundary is already held — `platform → services` is zero imports. **E1.4** makes it *enforced* rather than merely true |
| `services/structure/inference.py` | **Strategy behind a Port.** No boolean selects between "real" and "fabricated" | Port (`InferenceProvider`) · Chain of Responsibility (tier cascade) · Circuit Breaker (already real) | ❌ **E6.1.** Delete the `simulate: bool`. A flag that can be `False` while the code fabricates is the bug; constructor injection makes the fabricating path unreachable from the production package |
| `services/cover` | **Port + retrying adapter** | Port · Adapter · Retry with `Retry-After` honoured | ✅ **This is the exemplar.** `image_gen_port.py` is what every new external dependency should copy |
| `services/epub`, `onix`, `alttext` | — | — | ❌ **E7.2.** Fully built, zero callers. Wire or quarantine; do not leave ambiguous |
| `packages/api` | **Thin adapter over a repository, with a fail-closed middleware chain** | **Principal as a closed union** · route-declared auth requirement · Repository (`db.ts`) · Middleware chain | ❌ **E5.1.** Today the auth hook `return`s early for `/v1/admin` — fail-*open* by construction |
| `packages/web` | Out of scope for this plan beyond type-checking | — | **E0.4** type-checks it; no behavioural work |
| CI / `tools/` | **Gates that are proven able to fail** | Mutation testing applied to gates, not to code | ❌ **E0.1.** The generalisation of `L1`, and the highest-leverage item here |

### 1.1 Anti-doctrine — what this plan refuses to do

Carried forward from `ARCHITECTURE_UPLIFT_PLAN.md §2` and extended:

- **No DI framework.** Constructor injection already works; `build_registry(config)` is a
  function, not a container.
- **No async rewrite of the pipeline.** Batch PDF rendering is CPU-bound. Async buys
  nothing and costs the executor's one-loop simplicity. Concurrency stays horizontal
  (N workers on `FOR UPDATE SKIP LOCKED`).
- **No repository layer on the Python side.** CAS *is* the repository.
- **No Temporal migration.** `§2.4` sanctions the Postgres substitute and `worker.py`
  claims the exception correctly. It stays out of the ledger.
- **No microservice split.** The monolith is right at this scale.
- **No ORM.** `pg` + hand-written SQL is correct for eight tables; RLS does the work an
  ORM's tenancy layer would do, and does it where it cannot be bypassed.
- **No rewrite of `rules.py` or `publisher_stages/__init__.py` for size.** Both are god
  modules by line count. Neither is a defect the audit found. Splitting them touches
  everything else in this plan for no rubric movement.

---

## 2. Security and safety doctrine

The audit's one CRITICAL is a security finding, and three MEDIUMs are. This section
states the threat model the controls answer to, because `L5` exists precisely because
`ARCHITECTURE.md §2.1 P5` stated a control without stating what it defends against.

### 2.1 Threat model

| # | Asset | Threat | Present posture | Ledger |
|---|---|---|---|---|
| T1 | Worker host | Tenant-uploaded DOCX/DOC → zip bomb, XXE, OOXML relationship traversal, malicious embedded font, LibreOffice OLE2 parser exploit | **Container runs as root, writable rootfs, `CAP_*` intact, default bridge egress.** `libreoffice-writer` — a very large parser surface — runs in that context | `L5`, `L6` |
| T2 | Tenant data | Cross-tenant read via a query that forgets `loadOwned()` | Every current query calls it. Nothing *enforces* that the next one will. `artifacts` and `build_stages` carry no `tenant_id` at all | `L13` |
| T3 | Admin surface | A future `/v1/admin` route that omits its own `isAdminToken` check is unauthenticated | Global hook `return`s early for that prefix | `L12` |
| T4 | Egress | An exploited render stage exfiltrates the CAS, or an LLM adapter is steered to an internal address (SSRF) | Worker has unrestricted outbound network | `L5` |
| T5 | Supply chain | A swapped pandoc/typst release, an unpinned apt or pip dependency | `ARG PANDOC_VERSION=3.5` by tag, not digest — the Dockerfile's own `TODO(repro)` says so. No `--require-hashes`, no SBOM | `L6` |
| T6 | Secrets | Bearer tokens in a plaintext env var, compared against by every request | `PUBLISHER_API_TOKENS="token:tenant"`. Comparison *is* timing-safe (good). Storage is not | — |
| T7 | Availability | One 900-page manuscript OOMs the host | `mem_limit: 3g` / `cpus: 2.0` are set (good). Per-stage `memory_budget_mb` is declared on all 19 stages and enforced nowhere | `L10` |

### 2.2 Controls, and which are new

**Defence in depth is the rule: no single mechanism is load-bearing.** T2 gets both a
type-level barrier (`E5.2`) and a database barrier (`E4.2`), because either alone is one
mistake from failing.

| Control | Answers | Item |
|---|---|---|
| Non-root UID, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, seccomp profile | T1 | E3.1 |
| Worker on an `internal: true` network only — Postgres reachable, internet not | T1, T4 | E3.1 |
| `SandboxPort` actually invoked per stage, tier from the declaration | T1 | E3.2 |
| One zip-bomb implementation (`publisher_sandbox.check_zip_bomb`), not two | T1 | E3.3 |
| XML hardening audit of the OOXML path | T1 | E3.4 |
| `tenant_id` on every tenant-scoped table + RLS + `SET LOCAL app.tenant_id` | T2 | E4.2 |
| Least-privilege DB roles; app role is not table owner and lacks `BYPASSRLS` | T2 | E4.3 |
| `Principal` union, route-declared auth, **default `tenant` when undeclared** | T3 | E5.1 |
| Startup assertion: every registered route declares its auth requirement | T3 | E5.1 |
| Egress allowlist proxy for the one stage family that legitimately needs it | T4 | E3.1 |
| Digest-pinned pandoc/typst, `pip install --require-hashes`, SBOM + `pip-audit`/`npm audit` in CI | T5 | E3.5 |
| Tokens hashed at rest (argon2id), or OIDC introspection | T6 | E5.3 |
| `RLIMIT_AS` / `RLIMIT_CPU` from the declared budgets, real `deadline`, `TIMEOUT` error kind | T7 | E2.2 |

> `[UNKNOWN]` — **T1's XXE posture.** `services/ingest/publisher_ingest/docx_to_ast.py`
> was **not read** by the audit (budget) and is not read here. Whether it uses
> `defusedxml`, or `lxml` with `resolve_entities=False` and `no_network=True`, is
> unverified. `E3.4` is scoped as *audit then fix*, not *fix*. Do not assume either way.

---

## 3. Workstreams

Each item states what it **closes**, the **change**, and an **acceptance** condition.
The standing rule for all of them:

> **Every item lands with a test that fails before the change and passes after.** An
> item whose test passes before the change has not been demonstrated to do anything, and
> is exactly the failure mode `L1` is.

---

### E0 — Make the gates able to fail *(≈4 days)*

Nothing below E0 is verifiable until this lands. It also implements
`VERIFICATION_PLAN.md` G1–G8, which has been a draft with nothing implemented.

#### E0.1 — The meta-gate: prove every CI-blocking gate can fail
**Closes:** `L1`, and the whole class it belongs to. **This is the highest-leverage item in the plan.**

`L1` is not "the codegen gate is misconfigured". It is "a gate was declared blocking,
and nobody ever checked it could fail". Fixing the one instance leaves the class open.

Add `tests/meta/test_gates_can_fail.py` with a registry of every CI-blocking gate:

```python
GATES = [
    Gate(id="codegen-sync",   cmd=[...], mutation=mutate_schema_without_regen),
    Gate(id="dag-integrity",  cmd=[...], mutation=add_unsatisfiable_stage_input),
    Gate(id="schema-lint",    cmd=[...], mutation=add_freetext_field_to_structure_route),
    Gate(id="stage-versions", cmd=[...], mutation=change_stage_body_without_version_bump),
    Gate(id="service-deps",   cmd=[...], mutation=add_undeclared_cross_service_import),
    Gate(id="import-linter",  cmd=[...], mutation=import_tracer_bullet_from_worker),
    Gate(id="docs-claims",    cmd=[...], mutation=add_nonexistent_path_to_architecture_md),
]
```

Each test copies the repo to a temp worktree, applies the mutation, runs the gate,
asserts **non-zero exit**. Plus one test that parses `.github/workflows/ci.yml`, extracts
every step, and asserts each appears in `GATES` — so a gate added to CI without a
can-fail test **fails the meta-test**.

**Acceptance.** `pytest tests/meta` red on today's tree (`codegen-sync` cannot fail),
green after E0.2. Deleting any `GATES` entry turns the coverage test red.

#### E0.2 — Make codegen-in-sync real
**Closes:** `L1`

`.gitignore:29` excludes `*.gen.*`; the gate is `git diff --exit-code` over files git
does not track. `git ls-files schemas | grep -c 'gen\.'` → `0`.

Pick one — both work, do not do both:

- **(a) Track the generated files.** Remove `*.gen.*` from `.gitignore`, commit them,
  and the existing `git diff --exit-code` becomes a real gate. Matches the comment
  already sitting above the rule (*"checked in but must be regenerated"*), which is the
  clearest evidence of original intent. Cost: generated-file diff noise in review.
- **(b) Hash manifest.** Emit `schemas/.codegen-manifest.json` (sha256 per generated
  file), track *that*, and the gate compares regenerated hashes to it. No diff noise,
  one more moving part. Consistent with the repo's content-addressing doctrine.

**Recommendation: (a).** It restores the intent that is already written down, and it is
a three-line change rather than a new mechanism. `E0.1` guards it either way.

**Acceptance.** A deliberate schema edit without regeneration turns the `contracts` job
red. Verified by `test_gates_can_fail.py::test_codegen_sync`.

#### E0.3 — Postgres in CI
**Closes:** `L2`. **Executes:** `U9`'s written-but-never-run tests.

`grep -c 'services:' .github/workflows/ci.yml` → `0`. All 24 integration tests skip with
*"Postgres is not listening on localhost:55432"*. Worker durability, U8 admission
control and API hardening have never been executed by CI.

Add to the `python` job:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    env: { POSTGRES_DB: publisher, POSTGRES_USER: publisher, POSTGRES_PASSWORD: publisher }
    ports: ["55432:5432"]
    options: >-
      --health-cmd pg_isready --health-interval 2s --health-timeout 5s --health-retries 15
```

Then — critically — **assert the tests did not skip**. A CI run where 24 tests silently
skip is indistinguishable from one where they pass. Add `--strict-markers` and a
collection-floor assertion (the repo already has this pattern in `tests/`): fail if the
integration suite reports any skip whose reason matches `Postgres is not listening`.

**Acceptance.** CI runs 24 more tests than it does today, and a CI config change that
removes the service turns the job red rather than green-with-skips.

#### E0.4 — TypeScript is tested and type-checked
**Closes:** `L3`

~3.3k LOC including the entire auth and tenancy boundary: zero test files, and CI runs
neither `vitest` nor `tsc --noEmit` despite both being configured in `package.json`.

New `node` CI job, with the same Postgres service:

```yaml
- run: npm --workspace packages/api ci
- run: npm --workspace packages/api run lint      # tsc --noEmit
- run: npm --workspace packages/api run test      # vitest run --coverage
```

Minimum test set, chosen by blast radius rather than by coverage percentage:

1. **Auth matrix** — every route × {no token, malformed token, valid tenant token, valid
   admin token, valid token for a *different* tenant}. Table-driven.
2. **Route-coverage meta-test** — enumerate `server.printRoutes()`, assert every route
   appears in the auth matrix. This is `E0.1`'s idea applied to routes: a new route with
   no auth test fails the suite.
3. **Idempotency race** — two concurrent requests with one key; assert exactly one
   resource created and the second gets 409-or-replay (the N2 fix, currently untested).
4. **CAS path traversal** — `casPath` with `../`, absolute paths, null bytes (N5).
5. **Streaming bounds** — artifact download does not read the whole blob into memory (N4).

**Acceptance.** `tsc --noEmit` clean and gating. Deleting an `isAdminToken` check turns
the suite red — which is the test that `L12`'s fix is real.

#### E0.5 — A suite you can actually run
**Closes:** `L16`

`scripts/test.ps1`'s header claims *"338 collected, zero errors, 13.8 s"*. Measured
2026-09-09: **432 tests, 553 s**, one of which spends 180 s shelling out to Scribus and
then fails. A 9-minute suite is not run before every commit, so it stops being a gate.

- Mark external-binary tests `@pytest.mark.external` (Scribus, LibreOffice, any GUI
  binary). Deselected by default; run in a nightly job.
- Delete the hand-written timing claim from the header. Have `test.ps1` print measured
  wall time and `--durations=10`. **A number nobody regenerates is a future `[FALSE]` row.**
- Re-measure and record the new default-suite time in `COST_AND_STABILITY_PLAN.md §S1`
  as *measured on <date>*, not as a target.

**Acceptance.** Default suite under 90 s. `pytest -m external` still runs the full set.
`S1`'s claim carries a measurement date.

#### E0.6 — `services/alttext` in `PUBLISHER_PKGS`
**Closes:** `L17`. One line. The exact omission the variable's own comment was added to
fix for cover/idml/epub/onix.

---

### E1 — Boundaries: one home per concern *(≈5 days)*

#### E1.1 — Move the executor out of the dev harness
**Closes:** `L4`. **Absorbs:** `A4.1`

`worker.py:54` — `from tracer_bullet import DagExecutor`. Production imports its
executor from the module whose own docstring calls it the local dev harness.

Create `platform/exec/py/publisher_exec/`, and split the executor while moving it, because
moving it unchanged preserves the part that makes `E2` hard:

```python
# pure — no CAS, no DB, no clock. Unit-testable with a dict.
def plan(registry: FrozenRegistry, root_inputs: Mapping[str, Any]) -> ExecutionPlan: ...

# effectful — the only half that touches the world
def run(plan: ExecutionPlan, ctx_factory, middleware: Sequence[StageMiddleware]) -> BuildResult: ...
```

`tracer_bullet.py` becomes what it claims to be: a thin CLI over `publisher_exec` that
sets `allow_stub_engines=True`. `worker.py` imports the same package and does not.

**Why split rather than relocate.** Reachability, topological order and cache-key
derivation are today entangled with CAS writes and printing. Every `E2` item needs to
assert on scheduling decisions; with `plan()` pure, those become dict-in/dict-out tests
instead of integration tests.

**Acceptance.** `grep -rn 'from tracer_bullet import' --include=*.py .` returns only
tests of `tracer_bullet` itself. `import-linter` forbids the edge (`E1.4`).
`plan()` has unit tests that construct no CAS and open no socket.

#### E1.2 — Registry lifecycle: explicit construction, no import-time env
**Closes:** `L15`

Today: a module-global `_REGISTRY`, mutated at import time by four
`select_implementation()` calls — one of which reads `PUBLISHER_RENDER_ENGINE` — and
mutated *again* per build by `worker.run_build`. Import order and environment are part
of the graph's identity. That is temporal coupling in the load-bearing structure.

```python
@dataclass(frozen=True)
class RegistryConfig:
    render_engine: RenderEngine      # enum, not a lowercased string
    emit_idml: bool
    finish_impl: str = "finish-gs"
    ingest_impl: str = "ingest"

def build_registry(config: RegistryConfig) -> FrozenRegistry: ...
```

- `@stage(...)` keeps registering — but into an append-only `_DECLARATIONS` list that
  carries **no selection state**. Declaration stays a side effect of import (correct;
  it is data). *Selection* stops being one.
- `stages/__init__.py` becomes imports only. No `os.environ`, no `select_implementation`.
- Each entry point parses env **at its edge** and calls `build_registry(config)`:
  `worker.py`, `tracer_bullet.py`, `cli.py`, `integrity.py`.
- `FrozenRegistry` is immutable. `worker.run_build` cannot mutate it; if a build needs a
  different engine, it builds a different registry.

**Acceptance.** `grep -rn 'os.environ' stages/__init__.py` → 0. A test constructs two
registries with different engines *in the same process* and asserts both DAGs are
correct — impossible today. `check_integrity()` runs against an explicit config rather
than against whatever the import order produced.

#### E1.3 — Promote the shared AST→HTML renderer
**Closes:** `A4.2` (carried forward)

`paginate_stage` imports `stages.extract_stage._ast_to_html` and
`stages.design_compile_stage._emit_css` — private, cross-sibling. Extract to a public
shared module.

> **Constraint that must survive the refactor:** `extract` and `paginate` must call the
> **same function object**. Two copies silently break the guarantee that pagination
> renders exactly what the text-integrity gate verified. Add a test asserting identity,
> not just equality of output.

#### E1.4 — Declarative import enforcement
**Closes:** the enforcement gap behind `L4`, `L15`

The audit found no import-linter, no dependency-cruiser, no ArchUnit — in their place
three bespoke lints that are *better than most repos of this size carry*. Keep them; add
the declarative layer they cannot express.

`.importlinter`:

```ini
[importlinter:contract:no-platform-to-services]
type = forbidden
source_modules = publisher_stages, publisher_cas, publisher_cache, publisher_exec
forbidden_modules = publisher_ingest, publisher_structure, publisher_prepress, publisher_cover

[importlinter:contract:services-do-not-know-the-substrate]
type = forbidden
source_modules = publisher_ingest, publisher_structure, publisher_prepress, publisher_cover
forbidden_modules = publisher_stages, publisher_cas, publisher_exec

[importlinter:contract:services-are-independent]
type = independence
modules = publisher_ingest, publisher_structure, publisher_prepress, publisher_cover, publisher_epub, publisher_onix, publisher_alttext
# documented exception: publisher_agents -> publisher_structure.rules (U3, CI-linted)

[importlinter:contract:no-production-import-of-the-dev-harness]
type = forbidden
source_modules = worker, publisher_exec
forbidden_modules = tracer_bullet
```

`tools/lint_service_deps.py` keeps the one job import-linter cannot do: checking that
each cross-package import is *declared in the importing package's* `pyproject.toml`.

**Acceptance.** `lint-imports` in the `contracts` job, registered in `E0.1`'s `GATES`.
The audit's strongest structural property (`platform → services` is zero imports) stops
being a fact and becomes an invariant.

---

### E2 — Make the runtime enforce what the declarations promise *(≈4 days)*

Depends on `E1.1` (needs `publisher_exec`) and `E1.2` (needs a frozen registry).

#### E2.1 — Stage invocation middleware
**Closes:** the structural cause of `L10`, `L11`, and the insertion point for `L5`

Three separate defects (`deadline` unused, `memory_budget_mb` unenforced, `cache_key`
mis-populated) share one cause: there is no place where cross-cutting per-stage concerns
live. `run()` calls the stage function directly.

```python
StageMiddleware = Callable[[StageInvocation, Next], StageResult]

CHAIN = [metrics_mw, cache_mw, deadline_mw, memory_mw, sandbox_mw]  # outermost first
```

Each is a pure wrapper, independently testable, ordered once in the composition root.
`U8` admission control becomes `admission_mw` rather than a fifth special case.

#### E2.2 — Enforce deadlines and memory budgets
**Closes:** `L10`

`tracer_bullet.py:281` sets `deadline=datetime.now(timezone.utc)` — the present instant,
therefore always already expired — and `grep ctx.deadline` finds **zero consumers**.
`memory_budget_mb` is declared on all 19 stages, threaded into `ctx`, and enforced
nowhere. `ErrorKind` has no `TIMEOUT` producer.

- `deadline = started_at + timedelta(seconds=decl.timeout_s)`. `deadline_mw` enforces it
  — in-process via a watchdog, or (preferred, once `E3.2` lands) by
  `RLIMIT_CPU` on the sandboxed child, which cannot be ignored by a stage in a C
  extension.
- `memory_mw` applies `resource.setrlimit(RLIMIT_AS, budget)` in the child.
- Add `ErrorKind.TIMEOUT` and `ErrorKind.RESOURCE_EXHAUSTED`.

> **Decision required.** `ErrorKind.__post_init__` currently enforces that only `INFRA`
> and `EXTERNAL_LIMIT` may be retryable. Is a build that exceeded its memory budget
> retryable? **Recommendation: no** — retrying a deterministic OOM burns a worker slot to
> reach the same outcome, and `mem_limit: 3g` already exists as the host-level backstop.
> Make `TIMEOUT` retryable (often contention) and `RESOURCE_EXHAUSTED` not. Either way
> the invariant must be *extended deliberately*, not widened to let the new kinds through.

**Acceptance.** A test stage that sleeps past its deadline fails with `TIMEOUT`. A test
stage that allocates past its budget fails with `RESOURCE_EXHAUSTED`. Both assert on the
`ErrorKind`, not on a message.

#### E2.3 — Name the seed what it is
**Closes:** `L11`

`StageCtx.cache_key` is populated with the literal `f"tb-{stage}-v{ver}"`, not the cache
key computed ~20 lines later — in the executor the **worker** also uses. Two stages
(`prepress_stages.py:29`, `cover_stages.py:49`) hash it in `_deterministic_timestamp`.

The bug is not only that the value is wrong. It is that **the field's name describes a
use nobody makes of it.** Stages do not want a cache key; they want a stable seed.

- Remove `cache_key` from `StageCtx`. Add `deterministic_seed: str`, derived from the
  declared `cache_key_inputs` — the same derivation the cache uses, so the seed is
  genuinely stable across runs with identical inputs.
- `cache_mw` keeps the real cache key internal to itself, where it belongs.

**Acceptance.** Two builds with identical inputs produce byte-identical PDFs (the
`platform/reproducibility` contract, currently asserted by nothing on an executing path).
A build with different inputs produces a different seed.

---

### E3 — Container and input containment *(≈5 days)*

**Hard ordering: `E0.3` must land first.** The Postgres-backed integration suite is the
only thing that catches `read_only: true` breaking Ghostscript's, LibreOffice's and
fontconfig's temp writes. Hardening the container without it is how you ship a worker
that fails on every real build.

#### E3.1 — Harden the worker container
**Closes:** `L5` (the CRITICAL)

`Dockerfile.worker` has no `USER`; `docker-compose.yml`'s worker service sets `mem_limit`
and `cpus` (good) but no `read_only`, no `cap_drop`, no `security_opt`, and sits on the
default bridge with open egress — while parsing tenant-uploaded archives through
`libreoffice-writer`.

```dockerfile
RUN groupadd -r publisher && useradd -r -g publisher -u 10001 publisher
# pre-build the fontconfig cache as root so the read-only rootfs does not need to
RUN fc-cache -f
USER 10001:10001
```

```yaml
worker:
  read_only: true
  tmpfs:
    - /tmp:size=2g,noexec,nosuid,nodev
    - /home/publisher:size=256m,nosuid,nodev   # LibreOffice needs a writable HOME
  cap_drop: [ALL]
  security_opt:
    - no-new-privileges:true
    - seccomp:./infra/seccomp-worker.json
  networks: [dbnet]          # and nothing else — no egress

networks:
  dbnet:
    internal: true           # Postgres reachable; the internet is not
```

**Egress.** The worker does not need outbound internet today — the cover/LLM path is
unreachable in production (`L8`). When `E6` wires it, egress goes through an explicit
allowlist proxy on a second network attached **only** to the stages that need it, not to
the worker as a whole. That is the SSRF control (T4), and the reason to do it now is
that it is nearly free while the dependency does not exist and expensive once it does.

**Known breakage to expect and fix in the same PR** — this list is why `E0.3` comes
first: Ghostscript scratch files, LibreOffice profile directory, fontconfig cache,
weasyprint/Pango font lookups, `PUBLISHER_CAS_ROOT` ownership on the mounted volume.

**Acceptance.** `docker compose exec worker id` → `uid=10001`. A full real-manuscript
build completes green under `read_only: true`. `docker compose exec worker curl -m 5
https://example.com` fails. An integration test asserts each of those, so the hardening
cannot be silently reverted.

#### E3.2 — Give the sandbox a port and call it
**Closes:** `L6` (and makes `§2.1 P5` true rather than aspirational)

`platform/sandbox` defines `SandboxTier`, `ExitReason`, `SandboxResult` and a correct
object-capability model in its docstring — and is imported by nothing but its own tests.
The types are right; they have no call site.

```python
class SandboxPort(Protocol):
    def run(self, cmd: Sequence[str], *, input_dir: Path, output_dir: Path,
            budget: ResourceBudget, tier: SandboxTier) -> SandboxResult: ...
```

- `InProcessSandbox` — today's behaviour, **named so it cannot be mistaken for real
  isolation**, and refused when `allow_stub_engines=False`. Same doctrine as the
  stub-engine gate: a missing control must fail loudly, not silently certify.
- `RlimitSubprocessSandbox` — fork, `setrlimit(RLIMIT_AS|RLIMIT_CPU|RLIMIT_NOFILE|RLIMIT_FSIZE)`,
  chdir into the output dir, drop to the declared tier's capabilities.
- `sandbox_mw` (`E2.1`) selects by the stage's declared tier.

**Acceptance.** `grep -rn 'publisher_sandbox' --include=*.py . ` shows an executing-path
import. A stage declared `HEAVY` that tries to write outside its output dir fails with
`ExitReason.SECURITY`.

#### E3.3 — One zip-bomb implementation
**Closes:** the sub-finding inside `L6`

`ingest_stage.py:127,134` re-implements zip-bomb caps inline while
`publisher_sandbox.check_zip_bomb` exists unused. Two implementations of one security
control means one of them will be the one that gets fixed. Delete the inline copy, call
the shared one, keep the existing tests pointed at the new call path.

#### E3.4 — Audit the OOXML XML path *(scoped as audit-then-fix)*
**Answers:** T1

`docx_to_ast.py` was not read by the audit and is not read here. Verify, in this order:
entity expansion disabled (XXE, billion laughs), external DTD/network fetch disabled,
OOXML relationship targets resolved without path traversal, and archive member count /
name length / total uncompressed size bounded before extraction. If `defusedxml` is
already in use, this item closes as *verified, no change* — record that, because an
unverified assumption about XXE is exactly the kind of claim this repo's own doctrine
forbids.

#### E3.5 — Pin the supply chain
**Closes:** the `TODO(repro)` already in `Dockerfile.worker`; answers T5

- `pandoc` and `typst` by **sha256 digest**, verified with `sha256sum -c` — the
  Dockerfile's own TODO names this and names where the hashes belong (the toolchain
  manifest, beside the ICC and font hashes).
- Base image by digest, not `python:3.12-slim`.
- `pip install --require-hashes -r requirements.lock`.
- SBOM (`syft`) as a CI artifact; `pip-audit` and `npm audit --audit-level=high` as
  gates, both registered in `E0.1`'s `GATES`.

**Acceptance.** Rebuilding the image from the same commit produces the same tool
versions by hash. A tampered download fails the build.

---

### E4 — Data layer: enforce tenancy where it cannot be bypassed *(≈4 days)*

#### E4.1 — Migrations
**Closes:** `L14`

Schema is `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
mounted at `/docker-entrypoint-initdb.d/`, which Postgres runs **only against an empty
data directory**. An existing deployment has no path to a schema change.

Numbered immutable SQL files + a `schema_migrations` ledger + a ~40-line runner. Not a
framework — consistent with this repo's anti-framework doctrine, and `node-pg-migrate`
is the alternative if the runner starts growing features.

```yaml
migrate:
  build: { context: ., dockerfile: Dockerfile.api }
  command: ["node", "scripts/migrate.js"]
  depends_on: { postgres: { condition: service_healthy } }
worker:
  depends_on:
    migrate: { condition: service_completed_successfully }
```

**Acceptance.** A test starts Postgres with an *old* schema, runs `migrate`, asserts the
new shape. Re-running `migrate` is a no-op. `schema.sql` becomes `001_initial.sql` and
stops being edited in place.

#### E4.2 — `tenant_id` everywhere + Row-Level Security
**Closes:** `L13`; answers T2

`artifacts` and `build_stages` carry no `tenant_id`; `grep 'ROW LEVEL\|POLICY'` → `0`.
Isolation depends entirely on every future query remembering `loadOwned()` first. Today
every query does. Nothing makes that true tomorrow.

```sql
ALTER TABLE builds ADD CONSTRAINT builds_id_tenant_uniq UNIQUE (id, tenant_id);

ALTER TABLE artifacts     ADD COLUMN tenant_id TEXT;
UPDATE artifacts a SET tenant_id = b.tenant_id FROM builds b WHERE b.id = a.build_id;
ALTER TABLE artifacts     ALTER COLUMN tenant_id SET NOT NULL,
  ADD CONSTRAINT artifacts_tenant_fk FOREIGN KEY (build_id, tenant_id)
      REFERENCES builds (id, tenant_id);
-- same for build_stages

ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON artifacts
  USING (tenant_id = current_setting('app.tenant_id', true));
```

The composite FK is the part that matters: it makes an artifact row whose tenant
disagrees with its build's tenant **unrepresentable**, not merely unqueried.

API side: one `withTenant(tenantId, fn)` helper that issues `SET LOCAL app.tenant_id = $1`
at transaction start. Every route goes through it.

**Acceptance.** A test connects as the app role, sets tenant A, and `SELECT * FROM
artifacts` returns zero of tenant B's rows **with no `WHERE` clause in the query**. That
is the test that proves the database is doing the work, not the application.

#### E4.3 — Least-privilege roles
**Answers:** T2

RLS does nothing if the app connects as the table owner (owners bypass by default) or as
a role with `BYPASSRLS`.

- `publisher_app` — the API. Not owner. No `BYPASSRLS`. `FORCE ROW LEVEL SECURITY` on
  every tenant table.
- `publisher_worker` — cross-tenant by design (it claims from a global queue). Its claim
  query needs to see all tenants; **everything after the claim sets
  `app.tenant_id` to the claimed build's tenant** and runs under the policy. Scope
  `BYPASSRLS` to the claim, not to the worker's whole session.
- `publisher_migrate` — owner, used only by the migration container.

**Acceptance.** A test asserts `publisher_app` cannot `SET app.tenant_id` to a tenant it
was not authenticated as — i.e. the GUC is set by the auth layer, never by a handler.

---

### E5 — API boundary: fail closed *(≈3 days)*

#### E5.1 — `Principal` union, route-declared auth, fail-closed default
**Closes:** `L12`; answers T3

`plugins.ts:43` — `if (url.startsWith('/v1/admin')) return;`. The global hook delegates
authentication to each route body. Today's one admin route does check. The **next** one
is unauthenticated by default, and nothing catches it.

```ts
type Principal =
  | { kind: 'tenant'; tenantId: string }
  | { kind: 'admin' }
  | { kind: 'public' };

type AuthRequirement = 'tenant' | 'admin' | 'public';
```

- Routes declare `config: { auth: 'admin' }`.
- The hook reads `request.routeOptions.config.auth` and **defaults to `'tenant'` when
  undeclared** — a new route with no declaration rejects admin tokens rather than
  accepting everything.
- A startup assertion walks `server.printRoutes()` and **refuses to boot** if any route
  lacks a declaration. Fail-closed at deploy time, not at request time.
- The hook always assigns a `Principal`; it never `return`s early.

**Acceptance.** Adding a route without `config.auth` fails a test *and* fails startup.
An admin route reached with a tenant token gets 403. `E0.4`'s route-coverage meta-test
keeps the matrix complete.

#### E5.2 — Make an unscoped tenant query not type-check
**Answers:** T2, second layer

RLS (`E4.2`) is the runtime backstop. This is the compile-time one, so neither is alone.

Give `db.ts` a branded query type: tenant-scoped tables are reachable only through
`withTenant(...)`, and the raw `pool.query` export is not public. A handler that reaches
for a tenant table outside a tenant scope fails `tsc`.

**Acceptance.** A deliberate unscoped `SELECT * FROM artifacts` in a handler fails
`npm run lint`. Removing the RLS policy still leaves the type barrier, and vice versa.

#### E5.3 — Tokens hashed at rest
**Answers:** T6

`PUBLISHER_API_TOKENS="token:tenant"` in plaintext env, and in
`docker-compose.yml`. Comparison is already timing-safe — keep `tokensEqual`.

Minimum: store argon2id hashes; compare the presented token's hash. Better: OIDC
introspection, which `plugins.ts` already names as the P8 replacement seam. **Do the
minimum now; the seam is already correctly placed, so the upgrade stays cheap.**

---

### E6 — The inference layer: wire it or quarantine it *(≈4 days)*

#### E6.1 — Delete the `simulate` boolean
**Closes:** `L7`

`inference.py` gates `simulate=True` behind `PUBLISHER_ALLOW_SIMULATED_INFERENCE` and
assigns `self._simulate` at `:275` — then `classify()` at `:344` calls
`self._simulate_model_call(...)` **unconditionally**. `self._simulate` is never read
again. The U4 quarantine gates the constructor; the production shape
(`simulate=False`) still fabricates. `_simulate_model_call`'s own docstring says
*"FABRICATE a model result"*.

The fix is not to add the missing branch. **A boolean that selects between "real" and
"fabricated" is the defect** — it puts the two paths in one object where a missing `if`
silently chooses fabrication.

```python
class InferenceProvider(Protocol):
    def complete(self, request: InferenceRequest, route: RouteConfig) -> InferenceResponse: ...
```

- `OpenRouterProvider` ships in `publisher_structure`.
- `FabricatingProvider` moves to `services/structure/tests/` — **not shipped, not
  installed in the worker image**. A production process cannot import it.
- `create_gateway(provider)` takes it by constructor injection. The `simulate` parameter
  and the env var are deleted outright.

**Acceptance.** `grep -rn '_simulate' services/structure/publisher_structure/` → 0. A
test asserts the fabricating provider is not importable from the worker image's
`PYTHONPATH`.

#### E6.2 — Wire it, or say it is not wired
**Closes:** `L8`

`InferenceGateway` is constructed nowhere outside its own tests; `policy.yaml` is nine
routes of well-engineered versioned data with no production consumer;
`cover-brief` raises `BAD_INPUT` without a `brief_override`; `cover-judge` takes
verdicts as a root input.

**Recommendation: wire it as a guarded-reachable stage**, matching the pattern the repo
already uses correctly. A `structure-infer` stage with `optional_root_inputs` runs when
credentials are supplied and is cleanly absent when they are not. That is honest —
the capability exists, its reachability is declared — and it puts `policy.yaml`,
the circuit breaker and the tier cascade on an executing path where they can be tested.

`A3.1`/`A3.2` (bound concurrent LLM calls; async the inference path) fold in here, and
`E3.1`'s egress allowlist is what the network side of this looks like.

If the answer is instead *not this quarter*: say so in `ARCHITECTURE_ROADMAP.md` with a
date, and `E7.2` quarantines the package out of the production image. **Ambiguous is the
only wrong answer** — it is what produced `L8`.

---

### E7 — Make the baseline true, and keep it true *(≈3 days)*

#### E7.1 — Split normative from aspirational
**Closes:** `L18`, `L19`

`ARCHITECTURE.md §2.14` lists `packages/orchestrator/`, `packages/worker-render/`,
`services/design/` and `infra/`. None exist. `§2.3`/`§2.13` specify S3/R2;
`grep boto3|minio|S3Client` → `0` and `PUBLISHER_CAS_ROOT` defaults to
`./.publisher/cas`. (The Temporal half of `§2.3` **is** covered by `§2.4`'s documented
substitute and correctly stays out of the ledger — do not "fix" that half.)

- `ARCHITECTURE.md` becomes **normative**: it describes what exists and states the rules
  the code must hold to. Its §2.14 tree is generated from the filesystem.
- `ARCHITECTURE_ROADMAP.md` takes the unbuilt parts, each with a date and a trigger
  condition — the same discipline `A5`'s deferral table already uses well.
- The CAS abstraction is already the right seam for object storage. Say in the roadmap
  that S3/R2 arrives as a `CasBackend` adapter, not a rewrite.

#### E7.2 — Dead packages: wire or quarantine
**Closes:** `L9`

`services/epub`, `services/onix`, `services/alttext`: three packages with no stage, no
consumer, no inbound import from any executing path. `policy.yaml` carries an `alttext`
route nothing serves. The README calls them *"beta, less battle-tested"* — which is a
documented exception, so this is MEDIUM, not a bug. But "built, shipped in the image,
reachable by nothing" is not a state to leave ambiguous.

Per package, pick one and record it: **wire** (a stage, reachable or guarded) or
**quarantine** (moved under `contrib/`, excluded from the worker image, with a README
stating status and trigger). Either is fine. Silence is not.

#### E7.3 — Gate the docs
**Closes:** `L20`, and prevents `L18`/`L19`/`L20` recurring

`CLAUDE.md` says *"The 17 `@stage(...)` declarations"*; `grep -h '@stage(' stages/*.py |
wc -l` → **19**. That is a small error, and it is the whole problem in miniature: a
hand-written number that nothing regenerates.

`tools/lint_docs_claims.py` — check only mechanically checkable claims:

1. Every file path cited in `docs/*.md` and `CLAUDE.md` exists.
2. The `§2.14` repo tree matches the filesystem.
3. The stage count in `CLAUDE.md` matches the registry.
4. Every stage named in the `CLAUDE.md` task table resolves in the registry.
5. Every doc cross-reference (`§2.4`, `D8`, `U5`, `A3`) resolves to a real anchor.

Registered in `E0.1`'s `GATES` with a mutation that adds a nonexistent path.

**Acceptance.** Renaming a stage module without updating `CLAUDE.md` turns CI red. This
is what turns *"docs are accurate today"* into *"docs cannot silently stop being
accurate"*, which is the only version worth scoring.

---

## 4. Sequencing

```
E0  gates can fail            4d  ── nothing below is verifiable without it
     ├─ E0.1 meta-gate            (blocks: every acceptance condition in this plan)
     ├─ E0.2 codegen real
     ├─ E0.3 Postgres in CI       (HARD blocker for E3 — see below)
     ├─ E0.4 TS tested            (HARD blocker for E5)
     ├─ E0.5 fast suite
     └─ E0.6 alttext in PKGS
E1  boundaries                5d  ── E1.1 blocks E2 entirely
E2  runtime enforcement       4d  ── needs E1.1 (publisher_exec) + E1.2 (frozen registry)
E3  containment               5d  ── needs E0.3. E3.2 needs E2.1 (the middleware chain)
E4  data layer                4d  ── independent of E1/E2/E3; E4.2 needs E4.1
E5  API boundary              3d  ── needs E0.4; E5.2 pairs with E4.2
E6  inference                 4d  ── needs E1.2; E6.2 needs E3.1's egress design
E7  baseline truth            3d  ── E7.3 needs E0.1
                             ────
                              32d  ≈ 6–7 working weeks solo
```

### 4.1 Interaction check

Sequencing errors that cost real time, stated explicitly:

- **`E0.3` strictly before `E3.1`.** The Postgres-backed integration suite is the only
  thing that catches `read_only: true` breaking Ghostscript, LibreOffice and fontconfig
  temp writes. Hardening first means debugging a container that fails every real build
  with no test telling you which write broke.
- **`E1.1` strictly before `E2.x`.** Every `E2` item edits the executor. Doing them
  before the move means doing them twice, in two files.
- **`E0.1` before every acceptance condition.** Each item's acceptance is "a gate turns
  red". Without the meta-gate you are trusting that the gate could have turned red.
- **`E0.2`, `E0.3`, `E0.4`, `E0.6` all edit `ci.yml`.** One PR. Four PRs means three
  rebases.
- **`E4.2` and `E5.2` are the same control at two levels.** Land them adjacently so the
  "either alone is insufficient" test is written once, with both mechanisms present.
- **`E6.2` after `E3.1`.** Wiring an outbound LLM call before the egress policy exists
  is how the worker gets unrestricted internet permanently.
- **`E7.1` before `E7.3`.** Gating a document that is currently false just turns CI red
  on day one.

---

## 5. Verification

### 5.1 The rule

Every item lands with a test that **fails before** the change. An item whose test passes
before the change has demonstrated nothing — that is precisely the defect `L1` is.

### 5.2 Gate inventory at completion

| Gate | Job | Can-fail mutation |
|---|---|---|
| Full pytest (incl. integration) | `python` | — |
| No `Postgres is not listening` skips | `python` | remove the service block |
| Schema free-text lint | `python` | add a free-text field to a structure route |
| `cargo test` + `clippy -D warnings` | `rust` | — |
| Codegen in sync | `contracts` | edit a schema without regenerating |
| DAG integrity | `contracts` | add an unsatisfiable stage input |
| Generated contract tests | `contracts` | — |
| Stage version bump | `contracts` | change a stage body without bumping |
| Service deps declared | `contracts` | add an undeclared cross-service import |
| **`lint-imports`** | `contracts` | import `tracer_bullet` from `worker` |
| **`lint_docs_claims`** | `contracts` | cite a nonexistent path in `ARCHITECTURE.md` |
| **`tsc --noEmit`** | `node` | — |
| **`vitest run`** | `node` | delete an `isAdminToken` check |
| **Route auth coverage** | `node` | add a route with no auth declaration |
| **`pip-audit` / `npm audit`** | `contracts` | — |
| **SBOM emitted** | `contracts` | — |
| **Gates-can-fail meta-test** | `python` | delete a `GATES` entry |

Bold rows are new.

### 5.3 Re-score projection

| Dimension | Now | After | Because |
|---|---|---|---|
| Separation of concerns | 1 | **2** | `L4` executor in `platform/exec` · `L6` sandbox on the executing path · `L9` decided either way · `L12` one auth decision point |
| Consistency with baseline | 1 | **2** | `L18` `L19` `L20` closed by `E7.1`, kept closed by `E7.3` · `L7` flag deleted · `L1` gate real |
| Coupling | 1 | **2** | `L15` registry built from explicit config · `L4` forbidden by `import-linter` · `L11` field named for its use |
| Testability | 1 | **2** | `L2` `L3` `L16` · plus every gate proven able to fail |
| Resilience | 1 | **2** | `L5` container hardened, egress closed · `L10` budgets enforced · `L13` RLS + composite FK · `L14` migrations |

**Projected: 10 / 10.**

> **Caveat, stated rather than buried.** That projection is a *claim about this plan*,
> not a measurement. It becomes evidence only when the same EGFV v3.0 protocol is re-run
> against the tree afterwards by someone who did not write the plan. Two specific risks
> to the projection: (1) Consistency reaching 2 depends on accepting `§0.1`'s option (b)
> — if the reviewer holds `ARCHITECTURE.md`'s original scope as the baseline, Consistency
> caps at 1 until that system is built; (2) Resilience at 2 reads *"scalability
> demonstrated, not asserted"*, which needs `U9`'s full scaled run (50 builds,
> `--scale worker=3`, the SSE load test), not just the 12-build subprocess version that
> exists. `E0.3` executes what is written; the scaled version is still unbuilt.

---

## 6. Accepted debt — not in this plan, deliberately

| Item | Why not now | Revisit when |
|---|---|---|
| Temporal migration | `§2.4` sanctions the Postgres substitute; `worker.py` claims the exception correctly. Not drift | Queue depth or workflow complexity outgrows SQL |
| Three DesignSpec emitters + cross-emitter agreement gate | Months of work, no current user need. Moves to `ARCHITECTURE_ROADMAP.md` under `E7.1` | A second emitter has a customer |
| S3/R2 object storage | CAS is already the correct seam; swapping the backend is an adapter, not a rewrite | Multi-host deployment, or the local volume becomes the bottleneck |
| God modules (`publisher_stages/__init__.py` 523 LOC, `rules.py` 28k) | Not a defect the audit found. Splitting touches everything else here for zero rubric movement | After E0–E7 land, standalone |
| Full dict → generated-model conversion at stage boundaries | Large diff, low payoff, correctness risk. Do it opportunistically where a stage is already being edited (`A4.3`) | Never as a dedicated PR |
| ADR log | A real gap — the audit found no ADRs anywhere, which is why the intent baseline had to come from `ARCHITECTURE.md`. But writing twenty retrospective ADRs is archaeology | Start at the next decision worth recording. `E7.1`'s split is a good first one |
| `packages/web` behavioural tests | `E0.4` type-checks it; the review UI is not on the tenancy boundary | It handles anything a tenant can influence |

---

## 7. Honest limits of this plan

- **`E3.4` is scoped as audit-then-fix, not fix.** `docx_to_ast.py` was not read by the
  audit and is not read here. Its XXE posture is `[UNKNOWN]`. It may already be correct.
- **The day estimates assume the container hardening goes as expected.** `E3.1` has the
  widest variance in the plan: `read_only: true` plus LibreOffice plus fontconfig plus
  Ghostscript is four independent temp-write surfaces, and the estimate assumes `E0.3`'s
  integration suite finds them in one pass.
- **`E6.2` needs a product decision this plan cannot make** — whether the LLM layer is
  wired this quarter. Both branches are costed; the choice is not mine.
- **`E0.2` offers two options and recommends one.** If generated-file diff noise in
  review turns out to matter more than expected, (b) is the fallback and the meta-gate
  guards either.
- **One deviation from the audit's own rubric is carried forward.** The audit scored
  Coupling 1 rather than the rubric-strict 0 (`L4`'s blast radius is SYSTEM, but it is
  one symbol across a file-location boundary with no back-edge — not a cycle). If a
  reviewer overrules that, the starting score is 4/10, not 5/10. It does not change a
  single item in this plan.
