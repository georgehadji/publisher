# Publisher — Build Plan (v2.1)

Construction document: doctrine, module-by-module paradigm/pattern choices, cross-cutting engineering, phased delivery with gates.

Companions: [RESEARCH.md](RESEARCH.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [LLM_STRATEGY.md](LLM_STRATEGY.md) · [AGENT_DESIGN.md](AGENT_DESIGN.md) · [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) · [PARALLELIZATION.md](PARALLELIZATION.md) · [OPTIMIZATION.md](OPTIMIZATION.md).

> **v2 changes.** Folds in everything from the LLM, agent, and readiness work: a dedicated inference gateway and agent layer (§3.16–3.18), a learning subsystem with its own safety discipline (§3.19, §4.5), two new doctrine rules (D9, D10), routing/cost engineering (§4.4), a **parallel non-engineering track with real lead times** (§5.0) that will block engineering gates if started late, and a revised phase plan. Honest timeline moves from ~20 to **~26 weeks** to GA.
>
> **v2.1 changes.** Integrates [OPTIMIZATION.md](OPTIMIZATION.md) O1–O8 into the module catalogue and the phase plan rather than leaving them as a parallel wish-list. The load-bearing change: **O1 (renderer choice) is promoted from an optimization to a P0 decision gate** (§5.1 P0, §8), because it rewrites §3.9, §0's latency targets, P3's risk profile, and P5's infra budget — and because five people building against the wrong branch of it is the expensive failure. The rest sequence off it via the integration ladder in **§5.3**. Timeline is unchanged; O1 pays back inside P3 and P5, it does not extend them.

---

## 0. Non-functional targets (measurable, gate-enforced)

Nothing here is aspirational. Each becomes a CI assertion or an SLO alert.

### Build & runtime

| Dimension | Target | Measured by |
|---|---|---|
| Cold full build, 300pp novel | ≤ 150 s p95 (Chrome) · **≤ 40 s (Typst)** | build metrics |
| Warm design-tweak rebuild | ≤ 25 s p95 server-side · **≤ 1 s client-side preview** (O2) | cache-hit path |
| Cache hit ratio, iterative design session | ≥ 85 % | cache index |
| Peak worker RSS, 200 MB docx | ≤ 400 MB (extract), ≤ 1 GB (render Chrome) · **≤ 150 MB (render Typst)** | cgroup high-water |
| Hard memory cap per worker | 2 GB, OOM-kill → `infra` retry | cgroup limit |
| `paginate` p95, 400 pp | ≤ 90 s (Chrome) · **≤ 15 s (Typst)** · **≤ max(chapter)+stitch (O3)** | stage metric |
| `preflight` p95, 400 pp | ≤ 10 s | stage metric |
| Proof-PDF size vs press PDF (O4) | ≤ 40 % | artifact metric |
| Batch-stage compute on Spot/ARM (O5) | ≥ 80 % of batch CPU-seconds | fleet metric |
| API read p99 / write p99 | ≤ 150 ms / ≤ 400 ms | OTel |
| Reproducibility drift | 0 byte-diff on nightly rebuild | nightly job |
| Critical CVEs in shipped images | 0 | Trivy gate |
| Worker egress attempts | 0 (network namespace denies) | eBPF audit |

**The two-column latency and memory rows are the point of the P0 renderer gate.** The right-hand figures are the O1 hypothesis, not a commitment; the tracer bullet measures them on two real templates and the winning column becomes the SLO. Do not tune infrastructure against the Chrome column before that measurement — most of the Chrome column's cost (pool, admission control, memory-aware scheduling, the 1000-book soak's shape) exists only to serve it. See §5.1 P0 and [OPTIMIZATION.md §O1](OPTIMIZATION.md).

### Correctness

| Dimension | Target | Measured by |
|---|---|---|
| Text-integrity violations | **0, always** | hard gate |
| Preflight false-negative (vendor rejects our "green") | 0 per release | vendor submission test |
| Vendor rejections for file size (O4) | 0 — caught at build as `policy_violation` | preflight size budget |
| Chapter-title recall / precision | ≥ 0.95 / ≥ 0.98 | labeled corpus |
| Cross-emitter page-count agreement | ± 2 % | contract test |
| Page-raster SSIM vs golden | ≥ 0.995 | per template × profile |
| EPUB validation | EPUBCheck **and** ACE by DAISY both green | gate |
| IDML opens clean | InDesign, Affinity, Scribus | per release, manual |

### LLM & agent

| Dimension | Target | Measured by |
|---|---|---|
| LLM cost per title (novel / illustrated) | ≤ $0.20 / ≤ $0.45 | per-route cost telemetry |
| Prompt-cache read ratio on the classification prefix | ≥ 90 % | `cache_read_input_tokens` |
| Nodes escalated past tier 1 | ≤ 25 % | routing telemetry |
| Prose emitted by the structure service | **0, structurally impossible** | schema shape + assertion |
| Agent proposals accepted without edit | ≥ 70 % (Structure Wrangler) | review UI telemetry |
| Agent-caused preflight failures | 0 | preflight gate |
| Judge agreement with expert labels | ≥ 0.90 per dimension | calibration harness |
| Golden-case regressions from a learned change | **0** (ratchet) | promotion CI |
| Holdout slice excluded from agent assistance | ≥ 5 % of titles, permanently | routing telemetry |

### Reliability & security

Build success ≥ 99 % · RPO ≤ 5 min metadata, **0 for uploaded manuscripts** · quarterly restore drill, timed · third-party pentest passed before GA · zero secrets or manuscript text in logs (asserted in test).

---

## 1. Engineering doctrine

Applies to every module. Deviations need a written ADR.

**D1 — Functional core, imperative shell.**
Pure, total, deterministic functions in the middle (AST transforms, rules, scoring, geometry, style compilation). All I/O, process spawning, clocks, randomness at the outermost layer. Purity is what makes the content-addressed cache correct — an impure "pure" stage silently poisons it.

**D2 — Make illegal states unrepresentable.**
Algebraic data types + exhaustive matching. `TrimSize` is not `(float, float)`; `Sha256` is not `str`; `PageCount` is not `int`. Parse, don't validate: untrusted input crosses exactly one boundary and becomes a typed value or an error.

**D3 — Errors are values at boundaries.**
`Result[T, StageError]` between modules. Exceptions only inside a module. Every error carries `code`, `severity`, `sourceRef?`, `humanMessage`, `suggestedFix?`.

**D4 — Streaming by default, buffering by exception.**
Any function that takes a whole document into memory needs a comment justifying it. A 900-page anthology's `document.xml` is 150–250 MB; DOM-parsing it is an instant OOM.

**D5 — Deterministic or explicitly marked.**
No wall-clock, no `random`, no dict-iteration-order dependence, no locale-dependent sorting inside stage logic. Inject a `Deadline` and a `SeedlessRng` that throws.

**D6 — Bounded everything.**
Every loop has a max iteration count. Every queue has a depth. Every buffer has a cap. Every external call has a timeout derived from a propagated deadline, not a constant.

**D7 — Least authority.**
A module receives capabilities (a reader for *this* artifact, a writer for *that* prefix), never ambient access (an S3 client, a filesystem root).

**D9 — Model output is frozen data, never a live decision.** *(new in v2)*
Every LLM and agent call runs at a gate boundary, writes its result into a CAS artifact, and the build consumes the artifact. `model_id + prompt_version + schema_version` are part of that stage's cache key. Consequences, all load-bearing:
- Reproducibility survives a nondeterministic model.
- Inference is paid **once per (manuscript × inference version)**, not once per build. A customer trying 12 templates pays once.
- An agent's *proposals* are nondeterministic; the *accepted override ops* are deterministic build inputs. That is the only way an agent can live inside a byte-reproducible pipeline.
- **Model output may never enter a deterministic stage**: no pagination, no line-breaking, no typography fixpoint, no preflight verdict, no geometry.

**D10 — Learning is versioned, gated, and reversible.** *(new in v2)*
Learned state (exemplar-set hash, rule-set version, prompt version) is part of the toolchain digest, so a learned change invalidates cache **correctly** rather than silently altering old builds. Every promotion is a PR with corpus metrics. **Ratchet, never regress**: a change that improves the aggregate but regresses any golden case is rejected pending explicit review.

**D8 — Anti-patterns, explicitly banned.**
Shared mutable AST · service locator / global DI container · exceptions for control flow across modules · engine-local style defaults · "temporary" `--no-sandbox` · retry-on-`bad_input` · string-typed IDs · silent fallbacks that downgrade quality without telling the user · hidden network calls inside "pure" code · **an agent that writes to the AST instead of proposing override ops** · **a free-text field in the structure-inference schema** · **aggregate-only quality gates**.

---

## 2. Language allocation

| Tier | Language | Where | Why |
|---|---|---|---|
| Orchestration / IO-bound | **TypeScript** (Node 22+) | api, orchestrator, web, worker-render, agents | Playwright/Paged.js/Temporal SDK ecosystem; async IO fits |
| Document processing | **Python 3.12+** | ingest, structure, design, prepress, epub, idml, inference, learning | lxml, python-docx, PyMuPDF, pyvips, fonttools, Saxon bindings — irreplaceable |
| Hot paths | **Rust** | `cas-hash`, `docxstream`, `pagescan`, `pdfprobe`, `idmlwrite` | 10–100× on the CPU-bound 20 %; memory-safe on hostile input; PyO3 + napi-rs bindings |

Rust is used **narrowly and with discipline**: each crate ships a pure-Python reference implementation, and CI runs differential tests. If the Rust crate disagrees, it's the bug. No Rust until profiling proves the bottleneck — except `docxstream` and `pagescan`, known-hot from day one.

Free-threaded Python is watched, not depended on. Concurrency comes from process-per-job.

---

## 3. Module catalogue

Format per module: **responsibility · paradigm · patterns · core types · performance · failure · security · tests · done**.

---

### 3.1 `schemas/` — the contract

**Responsibility.** Own every inter-module data shape: AST, OverrideSet, DesignSpec, OutputProfile, PreflightReport, BuildManifest, Diagnostic, **ClassificationResult, AgentProposal, RoutingPolicy, PromotionRecord**.

**Paradigm.** Declarative, schema-first. Zero hand-written duplicate types.

**Patterns.** Single Source of Truth · Code Generation · Schema Evolution (additive-only within a major, semver per schema).

**Performance.** Codegen at build time. Validation uses a compiled validator (`fastjsonschema` / `ajv` with cached `compile()`), not reflective walking — 20–50× faster on hot paths.

**Security.** `additionalProperties: false` by default; cap array lengths and string sizes in-schema so a malicious AST can't be a memory bomb. **The structure-inference schema has no free-text field** — see §3.16.

**Tests.** Round-trip property tests; codegen-in-sync check; migration tests per version bump.

**Done.** `pnpm gen && git diff --exit-code` green; both languages import generated types only.

---

### 3.2 `platform/cas` — content-addressed store

**Responsibility.** Put/get immutable blobs by `sha256`. Local node cache in front of S3.

**Paradigm.** Functional core (key derivation pure), imperative shell (IO).

**Patterns.** Repository · Read-Through Cache · Facade over object storage · Capability handles scoped to one ref.

```rust
pub struct Sha256([u8; 32]);
pub struct ArtifactRef { hash: Sha256, media_type: MediaType, size: u64 }
```

**Performance.** Hash while streaming, never read-then-hash. `mmap` for local reads > 8 MB. **Node-local disk cache keyed by hash** — content-addressed means it's trivially correct and never invalidates; the single biggest latency win. Zero-copy handoff between same-node stages (pass a path). S3 multipart above 64 MB.

**Failure.** Write to `tmp` then atomic rename; the hash *is* the integrity check, so a torn read is detectable.

**Security.** SSE-KMS with per-tenant keys. Presigned URLs short-TTL, single-artifact. Nothing publicly readable.

**Done.** 1 GB blob round-trips with < 64 MB peak RSS.

---

### 3.3 `platform/cache` — build-graph memoization

**Responsibility.** Map `cache_key → artifact_refs`. Decide hit/miss. GC.

**Patterns.** Memoize · Repository · Sentinel for negative caching.

```python
def cache_key(stage: StageName, version: StageVersion,
              inputs: frozenset[Sha256], params: CanonicalJson,
              toolchain: ToolchainDigest) -> Sha256: ...
```

**Toolchain digest includes learned state** (D10): image digests ‖ fontset hash ‖ ICC hash ‖ hyphen-dict version ‖ engine semver ‖ **exemplar-set hash ‖ rule-set version ‖ prompt version ‖ model id**.

**Performance.** Single Postgres table, PK on key, covering index. Lookups batched per workflow. Hot keys additionally in Redis with a short TTL.

**Failure.** Stale entry from a behavior change that forgot a version bump. Mitigations: CI rule (touched stage dir ⇒ VERSION must change) + nightly cold-vs-cached byte-equality job.

**Security.** Tenant-scoped keys **only** for stages whose output can embed tenant data (fonts, metadata, tenant-scoped inference). Pure-content stages share globally — safe because the key is a hash of exact bytes, and a real win on common templates and on the classification system prefix.

**Done.** ≥ 85 % hit rate on a simulated 30-tweak design session.

---

### 3.4 `platform/sandbox` — hostile-input containment

**Responsibility.** Run an engine on untrusted input without letting it touch anything.

**Paradigm.** Object-capability. The sandbox exposes exactly: input dir (ro), output dir (rw), a CPU/mem/time budget. Nothing else.

**Patterns.** Facade over `runsc`/Firecracker · Bulkhead (per-engine pool) · Circuit Breaker on repeated engine crashes.

```
read-only rootfs · tmpfs /work (sized) · no network namespace (except q.external)
seccomp default-deny · non-root uid · all caps dropped · no-new-privs
cgroup: memory.max, cpu.max, pids.max · wall-clock kill · core dumps off
```

**Performance.** gVisor costs ~5–15 % syscall overhead — fine for ingest/prepress. For the Chrome pool prefer Firecracker or dedicated node pools with Chrome's own sandbox enabled; never `--no-sandbox`.

**Failure.** OOM-kill and timeout classified `infra`, retried once with doubled budget, then escalated as `bad_input` with real numbers.

**Tests.** Escape attempts *are* the test suite: a fixture that tries egress, one that fork-bombs, one that writes outside `/work`. Each must fail closed.

**Done.** eBPF audit shows zero egress packets from non-`q.external` workers under the full corpus.

---

### 3.5 `services/ingest` — OOXML → HTML typescript

**Responsibility.** `.doc`/`.docx` → sanitized `typescript.html` + normalized media + notes/fields sidecar.

**Paradigm.** Pipes-and-filters, streaming.

**Patterns.** Pipeline · Strategy (per format) · Chain of Responsibility (sanitizers, each link can reject) · Adapter (LibreOffice, Saxon, docx4j as processes behind ports).

**Performance — the memory-critical module.**
- `docxstream` (Rust): pull-parser over `word/document.xml` via `quick-xml`, streaming out of the zip. Never materializes the DOM. Constant memory regardless of size.
- Saxon XSLT 3.0 in **streaming mode** where the stylesheet allows; burst-mode only for parts needing lookahead.
- Media handled out-of-band: extracted to CAS as encountered, replaced with a ref. Images never sit in the document pipeline's memory.
- `pyvips` (streaming) for raster normalization — PIL loads whole images and will OOM on a 12000×9000 TIFF.
- Bounded parallelism across media: `min(4, cpu_quota)`.

**Failure.** LibreOffice hangs. Per-job `-env:UserInstallation`, hard kill, single retry, then `bad_input` with a "resave as .docx in Word" remedy.

**Security.** Sanitizer chain runs **before** any parser sees the file:
```
zip-structure → entry-count ≤ 10k → uncompressed ≤ 2 GB → ratio ≤ 100:1 →
path-traversal reject → drop vbaProject.bin/oleObject* → mime sniff →
XML: DTD off, external entities off, entity expansion off, depth ≤ 100 →
media: re-encode via pyvips with pixel cap, strip metadata
```
Assert DTD/entity settings in a unit test — a library upgrade can silently re-enable them.

**Done.** 200 MB docx extracts with ≤ 400 MB peak RSS; malformed corpus fails closed with actionable diagnostics.

---

### 3.6 `services/structure` — inference → AST

**Responsibility.** Typescript HTML → labeled, validated Book AST + confidence scores.

**Paradigm.** Pure functional. Every rule is `(Node, Context) → Option[Label × Confidence]`. Deterministic and total.

**Patterns.** Specification/Rule Object · Visitor · Strategy + Circuit Breaker (model behind a port; rules-only fallback) · Composite (the AST) · Zipper (cursor navigation for the review UI).

```python
Rule = Callable[[NodeView, DocContext], Verdict]     # pure, total
def infer(doc: Typescript, rules: Sequence[Rule]) -> tuple[AstDraft, list[Ambiguity]]: ...
```

**Model discipline.** Delegates to `services/inference` (§3.16). Sees **only** low-confidence nodes plus a small context window, batched ~40/call. Breaker open ⇒ degrade to rules-only and **tell the user** ("confidence lowered, please review chapters"). Never a silent downgrade.

**Text-integrity gate** — the module's reason to exist:
```python
assert normalize(text_stream(ast)) == normalize(text_stream(source))
```
Hard fail. No flag, no override, no config. Normalization = NFC + whitespace collapse + smart/straight quote folding, and *that* normalizer is itself property-tested. Defence in depth behind the schema shape (§3.16) which makes prose emission structurally impossible.

**Performance.** Single traversal, rules fused into one pass; AST built with structural sharing so override application is O(depth), not O(n).

**Done.** ≥ 0.95 chapter-title recall, ≥ 0.98 precision on the corpus; zero integrity violations.

---

### 3.7 `services/structure/overrides` — human and agent decisions

**Responsibility.** Store, apply, and rebase corrections without ever mutating the AST. **Also the agent's entire action space** (§3.17).

**Paradigm.** Event sourcing. Append-only op log; effective document is a fold.

**Patterns.** Command · Event Sourcing · Three-way Merge (rebase) · Memento (snapshot every N ops).

```python
@dataclass(frozen=True)
class OverrideOp:
    source_ref: SourceRef        # stable id + content hash
    op: Reclassify | Split | Merge | Suppress | SetAttr | Reorder
    actor: ActorId               # "user:8812" | "agent:structure-wrangler@v7"
    at: Timestamp                # recorded, never used in logic
    rationale: str | None        # agent proposals carry one; shown in the UI

def resolve(ast: Ast, ops: Sequence[OverrideOp]) -> EffectiveDoc: ...   # pure fold
```

**Rebase ladder** (new manuscript version): exact `sourceRef` → content-hash → normalized-text → fuzzy (token Jaccard ≥ 0.9) → **orphaned**, surfaced to the user. Never silently dropped, never silently reattached below threshold.

**Performance.** Fold is pure and cached as a build node. Structural sharing means 200 overrides on a 5000-node AST allocates ~200 paths, not a copy.

**Security & provenance.** Ops are attributed and immutable — the audit log for "who changed the author's chapter structure," human or agent. **This log is also the learning dataset (§3.19); retain and version it from day one.**

**Done.** A realistic v1→v2 revision preserves ≥ 90 % of overrides automatically; the rest are listed, never lost.

---

### 3.8 `services/design` — DesignSpec and its three compilers

**Responsibility.** One engine-agnostic design description → CSS, IDML style parts, Typst/TeX macros.

**Paradigm.** Declarative DSL + multi-target compiler.

**Patterns.** Interpreter/Compiler · Visitor per backend · Builder · Strategy · Prototype (template forking) · Flyweight (shared token tables).

```python
@dataclass(frozen=True)
class DesignSpec:
    typography: TypeScale          # base size, ratio, leading grid, measure
    page: PageGeometry             # trim, margins-by-pagecount curve, gutter, folio
    fonts: FontSelection           # refs into the vault, optical sizes
    elements: Mapping[Role, ElementRule]
    ornaments: OrnamentSet
    composition: CompositionParams # hyphenation zone, min word length, ladder limit
```

**The invariant.** No renderer holds a default. If Chrome and InDesign produce different leading, exactly one emitter is wrong. Contract test: page count ± 2 %, line-break decisions agree ≥ 95 %.

**Security — font licensing enforced here**, in the domain layer:
```python
def emit(self, spec) -> Result[StyleArtifact, PolicyViolation]:
    for f in spec.fonts.resolved():
        if self.target_use not in f.allowed_uses:
            return Err(PolicyViolation.font_not_licensed(f, self.target_use))
```
Fails as `policy_violation` naming the font and the missing right. Not a UI check — the UI can be bypassed by the API. **Emit a per-build font-licence manifest** so you can prove, per delivered PDF, which faces were embedded under which licence.

**Done.** Three emitters, one spec, agreement gate green on 10 templates × 3 profiles; licence manifest emitted per build.

---

### 3.9 `packages/worker-render` + `services/composition` — pagination

**Responsibility.** EffectiveDoc + styles + profile → paginated PDF + `pagemap.json`.

**Paradigm.** Imperative shell around a pure core (scan, score, plan adjustments).

**Patterns.** Port/Adapter per engine · Strategy · Object Pool (browsers) · Template Method · Bounded Fixpoint · **Scatter/Gather (chapter-parallel pagination, O3)**.

**Engine selection is data, decided at P0 (O1).** A DesignSpec declares `preferred_engine`; the port is identical either way. The default is set by the tracer-bullet measurement, not by this document:

| Engine | Role if O1 verifies | Role if it doesn't |
|---|---|---|
| **Typst** (`emit_typst`) | **Default.** Real paragraph-level composition, ~1/5 wall-clock, ~1/10 memory | Renderer C, STEM only (v2 plan) |
| **Chrome + Paged.js** (`emit_css`) | Compatibility path for templates whose design genuinely needs CSS | Default |
| **Prince / TeX** | Premium escape hatch | Premium escape hatch, **and the line-breaking mitigation** |

Keeping both is deliberate: a single-engine bet on a young ecosystem is the wrong risk for a print product, and the cross-emitter agreement gate in §3.8 only stays honest if two emitters are actually exercised. Neither engine holds a default — §3.8's invariant is unchanged and becomes *more* load-bearing, not less, when the two engines have genuinely different composition algorithms.

```python
def compose(doc, styles, profile, engine, max_iter=3) -> Composition:
    patch, best = EMPTY_PATCH, None
    for i in range(max_iter):
        pdf, pagemap = engine.paginate(doc, styles + patch, profile)   # impure
        defects = scan(pagemap)                                        # pure
        score = severity(defects)                                      # pure
        if best and score >= best.score: return best   # monotonic: never regress
        best = Composition(pdf, pagemap, defects, score, patch)
        if score == 0: return best
        patch = plan_adjustments(defects, styles)                      # pure, deterministic
    return best
```

`plan_adjustments` is pure and deterministic — **no RNG, no model, no clock** (D9). Every adjustment recorded in the manifest with its justification so a human can audit page 143.

**Performance — Chrome path (the expensive one).**
- Warm pool; **one `BrowserContext` per book**, destroyed after; browser recycled every N books (memory creep is real).
- Drive via **CDP `Page.printToPDF`**, never the CLI flag (different rendering path).
- All assets pre-resolved to local paths / data URIs — Chrome silently drops `url()` from `@page`, and the worker has no network anyway.
- Raise CDP timeouts and stream the PDF; a 600-page book blows the defaults.
- One book per process; `min(cores-2, pool_size)` processes; **memory-aware admission control** — reject if projected RSS > headroom rather than OOM at minute nine.

**Performance — Typst path.** No pool, no browser lifecycle, no CDP, no admission-control pressure worth modelling. A subprocess with a memory budget, like Ghostscript. If O1 verifies, **most of the bullet list above stops being infrastructure the team has to build and maintain** — that saving is the real O1 payoff, and it lands in P5, not P3.

**Both paths.** `pagescan` (Rust) does river/density analysis on rasterized columns with `rayon`, GIL released — second hot path after ingest, and **engine-independent by construction**: it reads rasters and a `pagemap`, so it is built once and is not on either side of the O1 fork. Build it on schedule regardless.

**Chapter-parallel pagination (O3).** Two additional registered stages:
```
paginate-chapter (×N, parallel) → per-chapter layouts + local pagemaps
        ↓
stitch → global folios, running heads, parity padding, TOC/index locators → pagemap.json
```
Sound for books specifically: a chapter starts on a fresh page (the recto policy already lives in the DesignSpec), intra-chapter layout is independent given a starting page parity, and the fixpoint optimizer never crosses a chapter boundary. Every genuine cross-chapter dependency is resolvable in the cheap stitch pass.

Two wins, and **the second is the durable one**: latency drops to `max(chapter) + stitch`, but more importantly the **chapter becomes the cache unit**, so editing chapter 7 invalidates chapter 7's layout alone. That is the revision loop — the actual daily workflow — and it stands on its own even if Typst makes the latency win moot.

**Guard.** Valid only when the design has no cross-chapter flow — true for novels, false for magazines and some illustrated non-fiction. Gate behind a DesignSpec capability flag; fall back to whole-book pagination when off. The flag is checked in the stage, not the UI.

**Failure.** Engine crash ⇒ `engine_bug`, capture the full input bundle to CAS for offline repro, no retry (a deterministic crash retries into the same crash). A `stitch` that finds chapter pagemaps disagreeing on parity is `engine_bug`, never silently repaired.

**Done.** 400 pp p95 within the target for the engine that won P0; raster diffs green; chapter-cache invalidation proven to touch exactly one chapter; pool stable over a 1000-book soak *if* Chrome is still the default.

---

### 3.10 `services/prepress` — color, geometry, PDF/X, preflight

**Responsibility.** Raw PDF → vendor-compliant PDF/X + a structured verdict.

**Paradigm.** Pure predicate rules over a thin PDF facade; effectful process adapter for Ghostscript.

**Patterns.** Specification · Chain of Responsibility (the GS filter chain) · Composite (rule groups per profile) · Adapter · Policy Object (vendor profile as data, never code).

```python
CHECKS = [FontsEmbedded(), NoRgbWhenCmyk(), BlackTextIs100K(), MinImagePpi(),
          BoxGeometry(), NoTransparencyForX1a(), InkCoverage(), OutputIntentPresent(),
          PageParity(), NoAnnotationsOrJs(), NoType3(), SpotColorPolicy(),
          FileSizeBudget(),                                  # O4
          …]
```

**`FileSizeBudget` is not cosmetic (O4).** Print-on-demand vendors impose upload size limits, and a 400 pp illustrated book at 300 dpi can exceed them. Without this check you discover it at 2 a.m. on the vendor's website; with it, it fails at build time as a `policy_violation` naming a remedy ("recompress figures / reduce image count / split volume") like every other rule. The limit is a field in the vendor profile — versioned data, not code — so the Vendor Watcher (§3.17) can PR a change when a vendor moves it.

**Two-artifact output policy (O4).** Never emit one compromise PDF:

| Artifact | Settings | Consumer |
|---|---|---|
| **Press** | no downsampling, all fonts embedded + subset, PDF/X-1a, OutputIntent, ICC | vendor upload |
| **Proof** | 150 dpi images, linearized, subset fonts, RGB, watermarked | the human, in a browser |

Font subsetting alone takes a multi-MB face to ~20 KB; the proof lands 60–80 % smaller with no perceptible loss at screen resolution, loads progressively via linearization, and is cheap to store, serve, and CDN. The press file is never opened by a human in a web viewer. Both are CAS artifacts from the same build node, so the proof is never stale relative to the press file — a failure mode of every "we'll generate a preview later" design.

```bash
gs -dPDFX -dBATCH -dNOPAUSE -dNOOUTERSAVE -dPDFXCompatibilityPolicy=1 \
   -sDEVICE=pdfwrite -sColorConversionStrategy=CMYK -dProcessColorModel=/DeviceCMYK \
   -dOverrideICC=true -sOutputICCProfile="$CMYK_ICC" -dRenderIntent=3 \
   -dDeviceGrayToK=true -dEmbedAllFonts=true -dSubsetFonts=true \
   -dDownsampleColorImages=false -dAutoFilterColorImages=false \
   -sOutputFile=out.pdf "$PDFX_DEF" in.pdf
```
`-dDeviceGrayToK=true` is not optional: black body text must be 100K, never 4-colour rich black.

**Performance.** `pdfprobe` (Rust) does a **single pass** collecting every metric all checks need. Naïve one-pass-per-check is 20 × a 400-page parse; this is 1.

**Failure.** Preflight is a **gate**. `severity=critical` ⇒ `policy_violation`, undeliverable. **No override flag exists in the API.**

**Security.** Ghostscript has a long CVE history — pin the version, strictest sandbox tier, `-dSAFER` semantics, no user-supplied PostScript (we author `PDFX_def.ps`).

**Done.** Zero vendor rejections on a green preflight across 20 real submissions — **including zero size-limit rejections**. Proof artifact ≤ 40 % of press size.

---

### 3.11 `services/idml` and `services/epub` — secondary writers

**Paradigm.** Pure AST → serialized package. **Patterns.** Builder · Visitor · Template Method.

**IDML.** `idmlwrite` (Rust) streams the zip; XML parts from templates with escaped substitution — never string concatenation of user text. Validate by round-tripping through `SimpleIDML` and, per release, opening in InDesign/Affinity/Scribus.

**EPUB.** EPUB 3 + accessibility metadata from the same AST. Gate on **EPUBCheck** *and* **ACE by DAISY** — both green or the artifact isn't produced. EAA conformance statement generated from the ACE report, never hand-written.

**Security.** Both are zip writers taking untrusted text — escape everything, cap entry counts, never emit a path from user data.

---

### 3.12 `packages/api` — the boundary

**Paradigm.** Hexagonal, CQRS-lite.

**Patterns.** Ports & Adapters · Command/Query separation · Idempotency Key · Outbox (webhooks published transactionally) · Rate Limiter · Circuit Breaker.

**Performance.** Uploads go **direct to S3 via presigned PUT** — bytes never traverse the API. Reads from denormalized read models. SSE for progress, not polling.

**Security.** OIDC + short-lived tenant-scoped tokens · Postgres row-level security on `tenant_id` · `Idempotency-Key` on every mutation · request-size caps · strict CORS · per-tenant KMS · append-only audit log.

**Done.** p99 read ≤ 150 ms; authz matrix (endpoint × role × own/other tenant) 100 % covered.

---

### 3.13 `packages/orchestrator` — workflow

**Paradigm.** Deterministic workflow code, effects in activities only.

**Patterns.** Saga · Compensating actions · Child workflows per output profile · Signal/await for human gates · Versioned workflow logic.

No I/O, no clock, no random in workflow code. Fan-out per profile as child workflows so one failing profile doesn't fail the build. Per-activity retry policy derived from the error taxonomy (§3.15).

**Done.** A build survives a rolling deploy mid-flight; a human gate held open 7 days resumes correctly.

---

### 3.14 `packages/web` — review UI

**Paradigm.** Unidirectional data flow; server state separate from client state.

**Patterns.** Container/Presentational · Optimistic UI on override ops · Virtualized lists · Command palette over the same command objects the API exposes.

**Performance.** Final-artifact page views are pre-rasterized artifacts from CAS, **served through a CDN** — they are immutable and content-addressed, which makes them perfect CDN objects and cuts both review latency and egress cost. Virtualize everything. Ship a compact structure projection, not the full AST.

**Client-side design preview (O2), gated on O1.** `typst.ts` compiles Typst in the browser via WebAssembly. The design-tweak loop is where users actually spend their time, and this takes it from the 25 s server round-trip to **sub-second at zero server compute** — no queue, no worker, no pool, no cache lookup. Three secondary wins, one of which is commercial:

- **Privacy.** A `no_external_llm` tenant with manuscript-confidentiality concerns can iterate on design without the text leaving the browser. This is a sentence in a procurement conversation, not just a feature.
- **Offline-capable review UI.**
- **Server load shifts** from "every tweak" to "final builds only" — a small fraction of requests.

**The constraint that makes it safe: preview is never the deliverable.** The final artifact is always built server-side from the same pinned engine version and is the only thing preflight and delivery ever see. If client and server engine versions diverge, previews lie — so **the client bundle's engine version is part of the toolchain digest and is asserted against the server's on load**, refusing to preview on mismatch rather than silently showing a different book. A preview that disagrees with the build is worse than no preview.

**New in v2 — instrumentation the learning system depends on:**
- **Raster-view tracking.** Record which page rasters the user actually looked at. A build shipped without viewing a page is **not** evidence that page is correct (§3.19).
- **Agent proposal outcomes.** accepted / edited / rejected, each recorded with the proposal id.

**Done.** 900-page structure review interactive at 60 fps; view-tracking and proposal-outcome events emitted and queryable; client preview sub-second on a 300 pp book and refusing to render on engine-version mismatch.

> **Careful with O2 and the learning system.** Raster-view tracking (§3.19's "silent accept is a trap") counts *server rasters the user looked at*. A client-side preview is a different surface and must emit its own view events, or O2 silently blinds the strongest weak signal the learning system has. Instrument the preview at the same time you ship it, not later.

---

### 3.15 Cross-cutting: error taxonomy

```
bad_input        user's fault       → no retry, actionable message + sourceRef
policy_violation gate failed         → no retry, names the rule and the remedy
engine_bug       our fault           → no retry, capture repro bundle, page on-call
infra            transient           → retry ×5 exponential + jitter
external_limit   429/quota/refusal   → honour Retry-After, per-tenant bucket, degrade path
```
Retrying `bad_input` burns money; retrying `engine_bug` retries into the same crash. Retry policy is a function of `kind`, not a decorator default.

---

### 3.16 `services/inference` — the model gateway *(new in v2)*

**Responsibility.** The single door to any LLM. Routing, batching, caching, structured outputs, refusal handling, spend ceilings, breakers, cost telemetry. **No other module talks to a model.**

**Paradigm.** Port/adapter with policy-as-data. Pure request construction; effectful call at the edge.

**Patterns.** Strategy (tier selection) · Cascade with confidence escalation · Circuit Breaker · Token Bucket per tenant · Bulkhead per route · Read-through prompt cache.

**Routing policy is versioned YAML, not code:**
```yaml
routes:
  structure.bulk:
    tiers: [claude-haiku-4-5, claude-sonnet-5]   # cascade
    escalate_when: confidence < 0.75
    effort: low
    latency_class: interactive
  structure.whole_book:
    tiers: [claude-opus-5]
    effort: medium
  alttext:
    tiers: [claude-sonnet-5]
    modality: vision
    latency_class: batch          # Batch API, 50% off
  design_recommend:
    tiers: [claude-opus-5]
    effort: medium
    latency_class: interactive
  diagnostics_prose:
    tiers: [claude-haiku-4-5]
    effort: low
```

**Prose contamination is made unrepresentable, not checked.** The structure schema is closed enums plus node ids, `additionalProperties: false`, every field required — **no `text`, `note`, or `reasoning` field exists**:

```python
class NodeLabel(BaseModel):
    node_id: str
    label: Literal["chapter_title", "part_title", "heading_2", "heading_3",
                   "body", "chapter_opening", "epigraph", "blockquote",
                   "verse", "scene_break", "dinkus", "caption", "footnote",
                   "front_matter", "back_matter", "false_positive"]
    confidence: float

class ClassificationResult(BaseModel):
    labels: list[NodeLabel]
```

A lint rule enforces "no string field except `node_id` in any structure-route schema." **Alt-text lives in a separate service** (§3.18) so this invariant stays absolute here.

**Performance and cost.**
- Rules-first: only low-confidence nodes reach a model, batched ~40/call, each carrying an excerpt + 2 neighbours + features.
- **Prompt caching**: taxonomy + exemplars in the system prefix with `ttl: "1h"`. Byte-identical across every book and tenant ⇒ globally shared. Lint the prefix for invalidators (timestamps, UUIDs, tenant ids, unsorted `json.dumps`). Verify via `cache_read_input_tokens`; alert on ratio regressions.
- **Minimum cacheable prefix differs by tier** — the cheapest tier's minimum is the largest (4096 tokens); a 3K prefix silently won't cache there (no error, just `cache_creation_input_tokens: 0`). Either grow the exemplar set past it or accept the miss where input is cheap.
- **Fan-out timing**: a cache entry is readable only once the first response starts streaming. Send one call, await its first token, then fire the rest — otherwise every parallel call pays full price.
- **Prefix prewarming (O7)**: the classification prefix is byte-identical across every tenant and every book, so on a 1-hour TTL a low-volume period lets it expire and the next customer pays to recreate it. A cheap keepalive on the prefix is strictly cheaper than the re-creation it prevents. Same argument, different layer: **`design-compile` results cache globally** — template × profile is a small finite set that every tenant shares.
- **Batch API for every non-interactive route** (50 % off): alt-text, index candidates, metadata, and all bulk backlist conversion.
- `effort: low` for mechanical classification; higher only for whole-book reasoning and design recommendation.
- Token estimates via the provider's `count_tokens`. **Never `tiktoken`** — wrong tokenizer, undercounts substantially.

**Failure.**
- **Always check `stop_reason == "refusal"` before reading content** — refusals return HTTP 200 with empty or partial content and crash anything indexing `content[0]`. Classified `external_limit`.
- Breaker open ⇒ rules-only + honest user-facing banner.
- Spend ceiling hit ⇒ rules-only + banner. **Never a silent quality downgrade.**
- Server-side fallbacks configured where supported.

**Security & privacy.**
- `no_external_llm` tenant flag routes to rules-only or a self-hosted model. **This is a procurement requirement, not a nicety** — publishers will ask whether unpublished manuscripts reach a third-party model and whether they're trained on.
- Prefer a zero-retention / no-training tier; the provider goes on the published subprocessor list.
- Per-tenant cache-key scoping for any route whose output can embed tenant data.

**Tests.** Golden request/response fixtures per route · schema-shape lint (no free-text fields) · refusal-path test · breaker and ceiling degradation tests · cache-key determinism fuzz · cost regression test on the corpus.

**Done.** ≤ $0.20 LLM cost per novel; ≥ 90 % prefix cache-read ratio; zero prose fields in any structure-route schema.

---

### 3.17 `services/agents` — the agent layer *(new in v2)*

**Responsibility.** Seven narrow agents, each with its own tool surface, eval set, and gate. **Not one general "book agent."**

| Agent | Trigger | Tools | Action space | Gate |
|---|---|---|---|---|
| **Structure Wrangler** | doc-wide rules confidence low | `query_nodes`, `sample_text`, `preview_structure`, `propose_override` | OverrideSet ops | Review UI, pre-populated |
| **Compositor** | defects survive the deterministic fixpoint | `scan_pagemap` (code exec), `render_range`, `crop`, `propose_adjustment` | DesignSpec patches, spread-scoped | Preflight + raster diff + human |
| **Preflight Explainer** | preflight produces findings | read-only over `preflight.json` + `pagemap` | text report only | none — cannot change the build |
| **Manuscript Doctor** | on upload, pre-ingest | read-only over `typescript.html` | advisory text | none |
| **Vendor Watcher** *(internal)* | nightly | web fetch, read `profiles/*.yaml` | opens a PR | human PR review |
| **Support Triage** *(internal)* | new ticket | repro bundle, manifest, logs | draft reply | human sends |
| **Corpus Synthesizer** *(internal)* | on demand | write to `corpus/` | synthetic manuscripts | reviewed as test data |

**Hard constraint (D9).** The action space is override ops and DesignSpec patches. **Never the AST, never the PDF, never a preflight verdict.** Every proposal carries a rationale and a before/after crop. `actor: "agent:compositor@v7"` sits in the same append-only log as human ops — attribution, rebase, and undo come free.

**Quality calibration — geometry first, model second, human third.**

| Defect class | Detector | Why |
|---|---|---|
| Widows, orphans, runts, hyphen ladders, short chapter ends, baseline drift, ink coverage, box geometry | **Deterministic pagemap scan** | Exact coordinates available; a model is strictly worse |
| Rivers | **Raster density scan** | Signal processing, not perception |
| Chapter-opener composition, drop-cap optical collision, legal-but-ugly figure placement, confusing table break, "page reads unbalanced" | **VLM on a crop, second opinion** | No closed-form definition — the residue |
| Is this template right for the book | **Human typographer** | Unchanged |

Benchmarks put VLMs at only moderate accuracy on layout-error detection, weak on subtle spacing and alignment and weakest where judgment is contextual. Using a VLM where geometry works is a regression.

**The vision loop that works:**
```
scan pagemap (script) → candidate defects with bboxes
  → crop(page, bbox) → image of just the suspect region
    → look → propose_adjustment (spread-scoped)
      → recompose + re-crop → verify or revert
```
Crop, don't render full pages — token cost is per-pixel and most of the page is irrelevant.

**Programmatic tool calling is mandatory, not an optimization.** A 400-page pagemap inspected page-by-page is 400 round trips and a cost model that doesn't close. The agent writes a script that runs inside code execution and calls the scan tools from within the loop; only the defect summary enters context. One turn.

**Tool-surface rule.** Promote from generic code execution to a **dedicated tool** when you need to gate, render, audit, or parallelize. `propose_override`, `propose_adjustment`, and `crop` are dedicated for exactly those reasons; pagemap munging is not.

**Cost and pacing.** Advisor pattern (cheap executor, expensive advisor for the plan; advisor must be ≥ executor) · **task budget** per repair session so it paces and wraps up gracefully (minimum 20k tokens) · context editing / compaction on long sessions rather than carrying a hundred crops · **explicit subagent cap** (current Opus delegates readily; an uncapped Compositor spawns one per spread) · `effort: low` for scanning, higher only for planning.

**Patterns.** Port/Adapter per tool · Bounded loop with monotonic score · Proposal/Approval (Command with deferred execution) · Bulkhead per agent.

**Tests.** Per-agent eval set with accept-rate and harm-rate metrics · replay harness over recorded trajectories · adversarial fixtures (agent must not propose an AST write; must not exceed subagent cap; must not exceed task budget).

**Done.** Structure Wrangler ≥ 70 % proposals accepted unedited; zero agent-caused preflight failures; zero AST writes.

---

### 3.18 `services/alttext` — accessibility text *(new in v2)*

**Responsibility.** Generate alt-text candidates for images from the AST + normalized media.

**Why separate.** It legitimately emits new text. Isolating it keeps the structure service's "no text fields, ever" invariant absolute and lintable.

**Paradigm.** Pure request construction; vision call through `services/inference` on the **batch** latency class.

**Constraints.** Alt text is *new descriptive text about an image*, never a transformation of author prose — a distinct integrity domain. Always human-approved before it reaches an EPUB. Downsample images when fidelity isn't needed; high-resolution vision is priced per pixel.

**Done.** ACE by DAISY green on images; approval UI ships with it.

---

### 3.19 `platform/learning` — the learning subsystem *(new in v2)*

**Responsibility.** Turn the override log into better rules, better exemplars, and fewer model calls — safely.

**Paradigm.** Offline, batch, gated. **Never live self-modification of a system that produces print-ready files.**

**Four loops:**
```
L0  in-episode        seconds     self-verification inside a run; nothing persists
L1  working memory    hours–days  per-title / per-tenant memory files
L2  consolidation     nightly     corrections → exemplars, skills, candidate rules
L3  distillation      weekly+     mined deterministic rules → the model call is deleted
```

**L1 memory** is a filesystem the agent reads and writes with ordinary file tools. We own the backend, so scoping, retention, and audit are ours:
```
/memories/
  title/<title-id>/     "this author uses ~~~ as a scene break; verse in ch.4, 9, 12"
  tenant/<tenant-id>/   house style: chapter openers verso, oldstyle figures in body
  global/               read-only to the agent — promoted knowledge only
```
Rules: never write author prose verbatim beyond the minimum to identify a pattern; never write credentials; per-tenant isolation is a hard boundary; memory is covered by the same deletion guarantee as everything else; **an explicit forgetting policy** (the part teams skip, and why agent memory rots).

**L2 consolidation pipeline** — copy the validated shape wholesale, including the parts people drop:
```
trajectories + corrections
  → extract recurring patterns
  → formalize as skill / exemplar / candidate rule
  → VALIDATE against held-out cases
  → deploy
  → MONITOR precision in production
  → RETIRE on degradation or conflict
```
Without validation and retirement you get the documented failure modes: skill conflict, overfitting to a handful of samples, degradation under distribution shift, validation gaps on rare inputs.

**L3 distillation — the point.** Every recurring correction expressible as a deterministic rule becomes one. Each mined rule permanently deletes a class of model calls: cheaper, faster, reproducible, CI-testable. **The agent's job is to make itself unnecessary.** The tracked metric is *model calls removed per month*, not intelligence added.

**Promotion ladder:**
```
episode scratchpad → title memory → tenant profile → global exemplar → deterministic rule
```
Thresholds, tuned but never zero: ≥ N occurrences, across ≥ K distinct titles, from ≥ 3 distinct tenants. Two gates carry real weight:
- **tenant → global** requires a **cross-tenant generalization check**. Publisher A's house style becoming everyone's default is a quality bug *and* a confidentiality incident.
- **global → rule** requires zero regressions on the frozen golden corpus.

**Signal taxonomy** — feedback is not uniform:

| Signal | Source | Strength |
|---|---|---|
| Explicit override | review UI | **Strongest** — a clean label |
| Rejected agent proposal | proposals UI | Strong negative |
| Re-edit after accepting | override log | Strong negative, delayed |
| Preflight failure after an agent adjustment | preflight gate | Hard negative |
| Vendor rejection on a green build | delivery | Hardest negative; rare and expensive |
| Typographer template review | internal | Gold label |
| Support ticket | support | Negative, unlabeled — triage first |
| **Silent accept** | build log | **Weak, and a trap** |

**Silent accept is the trap.** Absence of correction is not evidence of correctness — most customers do not know what good typesetting looks like, which is why they're buying this. Count it as positive **only if the user actually viewed the relevant page raster** (§3.14 instrumentation). Un-viewed = no signal, not approval.

**Evaluation discipline:**
- **Frozen golden corpus, never learned from.** Versioned, held separate, the only thing that gates promotion.
- Held-out split from the override log, rotated.
- **Multi-judge consensus for subjective calls**, with **distinct lenses** (typographic correctness / reading experience / spec compliance) — not three copies of one prompt. Consensus panels report materially higher agreement with experts than any single judge.
- **Judges are production code.** Calibrate against an expert-labeled ground-truth set, measure agreement dimension by dimension, adjudicate disagreements, feed the outcome back into the rubric. Test the documented failure modes: label-flip accuracy, paraphrase/format invariance, verbosity bias, stochastic stability, ordinal calibration.
- **Every promotion is a PR** with before/after corpus metrics; CI blocks regressions.
- **Ratchet, never regress** (D10).

**Failure modes and guards:**

| Failure | Guard |
|---|---|
| Skill conflict | Namespaced skills, explicit precedence, conflict detection in validation |
| Overfitting to few samples | Occurrence + distinct-title + distinct-tenant thresholds |
| Degradation under distribution shift | Continuous per-rule precision monitoring, auto-retire below threshold |
| Validation gaps on rare inputs | Permanent "unusual manuscript" bucket in the corpus |
| Cross-tenant style leakage | Generalization check at the tenant→global gate |
| **Feedback-loop poisoning** | Agent proposals shape what users see, which shapes what they correct — a closed loop that drifts. **Hold out ≥ 5 % of titles from agent assistance permanently.** Without an unbiased baseline you cannot distinguish improvement from drift, and every metric will tell you the comforting version |
| Model upgrade invalidates calibration | Treat like an engine upgrade: full corpus re-run, judge re-calibration, raster diffs |
| Learned state breaks reproducibility | Exemplar-set hash and rule-set version in the toolchain digest (D10) |

**Done.** Promotion CI green; holdout slice live and reported; judge agreement ≥ 0.90 per dimension; a measurable count of model calls removed.

---

### 3.20 `platform/stages` — the stage registry *(new in v2)*

**Responsibility.** Hold every stage's declaration as data, and derive from it: the DAG, the contract tests, the cache-key inputs, the local dev harness, and admission-control parameters. See [ARCHITECTURE §2.8.1](ARCHITECTURE.md).

**Paradigm.** Declarative registration + code generation. Zero hand-wired orchestration.

**Patterns.** Registry · Declarative configuration · Generated tests · Facade (`pub run-stage`).

```python
@stage(
    name="finish", version=9,
    inputs={"pdf": "raw-pdf/1", "profile": "profile/1"},
    outputs={"pdf": "pdfx/1", "report": "finish-report/1"},
    toolchain=["ghostscript", "icc"],
    fixtures="fixtures/finish/v3",
    memory_budget_mb=512, queue="q.prepress",
    placement="arm-spot",              # O5 — see §4.6
)
def finish(ctx: StageCtx, pdf: ArtifactRef, profile: OutputProfile) -> StageResult: ...
```

**Why it exists.** Without it, "every stage is independently developable against fixtures" is a convention people drift from under deadline. With it, a stage whose output stops matching its declared schema **cannot merge**, and a new stage joins the DAG without anyone touching the executor. It converts the parallelization plan from a process agreement into a mechanical property.

**Performance.** Registry resolution at import time; generated tests run per-stage in CI, parallel by construction.

**Failure.** A declared input schema with no producing stage, an orphan, or a cycle fails CI (DAG integrity check). A missing fixture set fails registration.

**Security.** `queue` and `memory_budget_mb` feed sandbox tiering and admission control — a stage cannot accidentally run in a laxer sandbox than declared. `placement` (O5) is declared here for the same reason: a stage cannot be scheduled onto interruptible capacity unless its declaration says its interruption semantics hold.

**Tests.** DAG integrity · generated contract tests over every fixture set · `pub run-stage` smoke test for every registered stage · version-bump CI rule (touched stage dir ⇒ `version` must change).

**Done.** Every stage registered; contract tests 100 % generated (zero hand-written); `pub run-stage <name> --fixture <v>` runs any stage in isolation in ≤ 60 s with no other service running.

---

## 4. Cross-cutting engineering

### 4.1 Concurrency & memory model

| Layer | Model | Rationale |
|---|---|---|
| API | async single-process, cluster by core | IO-bound |
| Orchestrator | Temporal workers, async activities | IO-bound |
| Doc workers | **process per job** | hostile-input isolation, GIL sidestep, clean OOM blast radius |
| Inside a job | bounded parallelism `min(4, cpu_quota)` | avoids thrash |
| Rust hot paths | `rayon`, GIL released | true parallelism where it pays |
| Agent sessions | one per build, bounded task budget, capped subagents | prevents runaway cost |

**Memory budget declared and enforced per stage.** Admission control rejects a job when projected RSS exceeds node headroom — a clean "queued, waiting for capacity" beats an OOM-kill at minute nine. Projection from a model fitted on `(page_count, media_bytes, node_count)`.

Backpressure end to end: queue depth → autoscaler → admission control → API returns `202` with a queue position, never a silent unbounded accept.

### 4.2 Observability

OpenTelemetry traces span workflow → activity → subprocess. Every stage emits `duration_ms`, `peak_rss`, `bytes_in/out`, `cache_hit`, plus domain metrics (`pages`, `defect_score`, `preflight_findings`). Every intermediate artifact retained and addressable — "reproduce customer bug" is `restore manifest → re-run from stage N`.

**SLOs:** build success rate · p95 build latency · cache hit ratio · **preflight false-negative count (alert on any non-zero)** · LLM cost per title · prompt-cache read ratio · agent proposal accept rate.

### 4.3 Supply chain & release

Images pinned by **digest**, never tag. SBOM per image, scanned, CI gate on critical CVEs. Signed images with an admission policy rejecting unsigned. Dependency bot for libraries, but **engine upgrades (Chrome, Ghostscript, LibreOffice, Saxon) and model upgrades are deliberate release events** — they change the toolchain digest, invalidate cache, and must pass the full golden corpus with raster diffing before merge. Never let a bot merge these.

### 4.4 Cost & routing engineering *(new in v2)*

- Per-call telemetry: `model_id`, `route`, `effort`, `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, resulting artifact hash. Rolled up per tenant and per route.
- **Alert on cache-hit-rate regressions** — a dropped ratio is usually a prefix bug and shows up as a cost spike before anyone notices the code change.
- Per-tenant spend ceilings on both model and Adobe API, with **visible** degradation.
- Batch API for every non-interactive path.
- Cost regression test on the corpus in CI: a PR that raises per-title inference cost by > 20 % fails and must be justified.

### 4.5 Data protection

Per-tenant KMS keys; manuscripts encrypted at rest. Retention: source per contract, intermediates GC'd, delivered artifacts retained (customers reprint). **Deletion actually deletes** — CAS, cache index, read models, logs, memory files, and backups, within the stated window, with a verification job. Export path so a customer can take manuscript + AST + DesignSpec + artifacts and leave. Engineers cannot read customer manuscripts by default; break-glass is logged, time-boxed, notified. **Staging and dev never contain real customer manuscripts.**

### 4.6 Compute placement *(new in v2.1 — O5)*

**Why this pipeline is unusually Spot-safe, and most aren't.** Spot capacity is normally unusable for real work because an interruption means lost progress. Here **every stage is idempotent and content-addressed**: an interrupted stage loses nothing but the CPU-seconds since its last cache write, and re-running it produces a byte-identical artifact. The architecture already paid for that property for reproducibility reasons — Spot is where it cashes out a second time. ARM (Graviton) compounds on top: ~19 % cheaper per vCPU and 20–40 % better price-performance on CPU-bound work, which is what every batch stage is.

| Workload | Placement |
|---|---|
| ingest, prepress, rasterize, EPUB/IDML writers, batch inference, nightly jobs | **ARM + Spot** |
| interactive render, API, orchestrator | on-demand (ARM where the image supports it) |
| anything with an external SLA, anything behind `q.external` | on-demand |

**Placement is a field on the stage declaration** (§3.20), not a deployment concern — the registry already carries `queue` and `memory_budget_mb`, and `placement` belongs beside them so a stage cannot be scheduled somewhere its interruption semantics don't hold.

**The ARM64 spike is the gating unknown, and it is pulled left to P1.** Chrome, Ghostscript, LibreOffice, and Saxon must be verified for ARM64 image availability *and* performance — published evidence on these specific tools is thin, so treat it as a measurement, not an assumption. The spike costs a day during P1, when the images are being built anyway; deferring it to P5 means discovering in week 18 that one engine has no usable ARM64 build, after the fleet plan assumed it.

**Interaction with O1.** If Typst wins P0, the single worst ARM64 risk (Chrome) drops off the critical path for batch work entirely.

---

## 5. Delivery

### 5.0 Track B — non-engineering, starts week 1 *(new in v2)*

These have real lead times and **will block engineering gates if started late**. Run them in parallel from day one.

| Item | Start | Needed by | Why it blocks |
|---|---|---|---|
| **Golden corpus licensing** — 50 real manuscripts, cleared for internal test use | Week 1 | P0 | You cannot build the corpus from scraped text. Blocks *every* correctness gate |
| **Typographer retainer** — gate owner for templates | Week 1 | P3 | 8 templates + review; ~$8–15k. The P3 blind A/B fails without them, and it's the highest-ROI spend in the project |
| **Vendor accounts + test titles** (KDP, IngramSpark) | Week 1 | P1 | The P1 gate is a real submission. Account setup and title creation are not instant |
| **Font strategy** — OFL set curated; any premium faces licensed for server-side generation + PDF + ebook embedding | Week 2 | P2 | `design-compile` refuses unlicensed faces; without a curated set the pipeline can't emit anything |
| **ICC profiles** — redistribution rights confirmed | Week 2 | P1 | Ships inside the image |
| **Legal pack** — ToS, AUP, privacy policy, DPA, subprocessor list (model provider included) | Week 4 | P5 | Beta signup without these is a liability |
| **Pentest booking** | Week 6 | Before GA | 6–8 week lead time is normal |
| **E&O insurance + print-guarantee terms** | Week 10 | Before GA | The guarantee is a sales asset; it must be insured before it's published |
| **Physical POD proof loop** | P1 onward | Quarterly + pre-GA | Order a real printed copy. Catches ink, paper, gutter, spine reality that no file check can |

### 5.1 Track A — engineering phases

Each phase ends at a **gate**. No gate, no next phase.

**P0 — Skeleton & spine (3 wks)**
Schemas + codegen · CAS + cache key + stage contract · sandbox baseline · Postgres DAG executor · ingest (docx only) · naive AST · **both renderers on one template** · one trim size · golden corpus repo + raster-diff harness stub · **synthetic corpus generator (O8) started**.
**Gate.** A plain novel `.docx` → 6×9 PDF: correct page count, running heads, recto chapter openers. Cache demonstrably skips unchanged stages.
**Gate — renderer decided (O1).** Two templates (one plain literary, one with sidebars/figures) ported to both `emit_css()` and `emit_typst()`; page-raster SSIM, line-break agreement, wall-clock, and peak RSS measured. **The default engine is chosen here, on data, and written into the DesignSpec default.** This is the only gate in the plan that exists to prevent parallel work rather than to validate it — see §5.3.

**P1 — Print compliance (3 wks)**
Vendor profiles as versioned data · Ghostscript CMYK + PDF/X-1a + OutputIntent · geometry (bleed/trim/marks/gutter curve/spine) · `pdfprobe` + preflight rule engine + hard gate · **`cover` stage computes geometry from final page count only — art generation is a separate, page-count-*free* track starting this phase (see below and [COVER_DESIGN.md](COVER_DESIGN.md) §0)** · font-licence manifest per build · **two-artifact PDF policy + `FileSizeBudget` in the rule set (O4, 3 days)** · **ARM64 engine spike (O5 prerequisite, 1 day)**.
**Gate.** IngramSpark accepts on first upload. Green preflight ⇒ **zero vendor rejections across 5 real submissions**, size-limit rejections included. First physical POD proof ordered. ARM64 viability known for all four engines — recorded as a decision, whether or not it is acted on yet.

**P1c — Cover design track, starts parallel with P1** *(new — [COVER_DESIGN.md](COVER_DESIGN.md))*
Runs from title metadata + DesignSpec alone, with **zero dependency on interior page count** — the corrected reading of the dependency edge in [ARCHITECTURE.md §1.3](ARCHITECTURE.md#13-critical-dependency-edge-people-get-wrong): only geometry and final assembly are page-count-bound, art generation is not. `schemas/cover/*` (ArtBrief, art-provenance, cover-verdict, closed-enum) · `services/cover` (art policy + capability validation against a pinned, scheduled-sync model catalogue — never a request-time model lookup · `ImageGenPort` adapter boundary, OpenRouter `POST /api/v1/images` + a fake for tests · deterministic per-model-dialect prompt rendering · deterministic pre-panel gates: thumbnail legibility, type-zone contrast) · `stages/cover_stages.py` (`cover-brief`, `cover-art`, `cover-judge`, `cover-compose`, `cover-preflight`) · `platform/routing/policy.yaml` (first real instance of the "routing policy is data" principle, §4.4, applied end to end — extended cache-key discipline for variant-qualified slugs + `reasoning` config, since a colon-suffix or an omitted reasoning object is itself an unhashed config input that can poison the cache silently).
**Gate.** A synthetic title produces a validated, capability-checked dispatch panel with zero live calls (tier resolution + capability validation run entirely offline against the pinned catalogue). `cover-compose` refuses to proceed without a `cover-geometry/1` artifact from the (now page-count-bound) `cover` stage. No image model output ever reaches `cover-compose`'s type layer — enforced by the closed `negative` enum mandating `text`/`readable-typography` on every `ArtBrief` (COVER_DESIGN.md §5).

**P2 — Inference & review (4 wks)**
Rules engine · **`services/inference` gateway** (routing YAML, closed-enum schemas, prompt caching + **prefix prewarming (O7)**, batching, refusal handling, breaker, spend ceiling, cost telemetry) · **text-integrity gate** · override layer + rebase ladder · review UI **with raster-view and proposal-outcome instrumentation**, rasters **CDN-served** · revision loop · font vault + licensing enforcement in `design-compile` · **client-side preview (O2) if Typst won P0**, instrumented for view-tracking from day one · **cover-brief and cover-judge wired to the inference gateway** (`platform/routing/policy.yaml`; cover-judge runs on the Batch API — real 50%-off `:batch` variants confirmed live on OpenRouter, [COVER_DESIGN.md §6.3](COVER_DESIGN.md#63-the-batch-api--corrected)) · **Human Gate 3 (cover selection)** added alongside the existing structure-review and quality-review gates, panel-ranked survivors only.
**Gate.** 50-manuscript corpus: ≥ 0.95 chapter recall, 0 integrity violations, v1→v2 revision preserves ≥ 90 % of overrides. LLM cost ≤ $0.20/novel. Prefix cache-read ratio ≥ 90 %. Zero free-text fields in any structure-route schema. Preview refuses to render on engine-version mismatch. **Cover: reasoning config explicit (never inherited) on every OpenRouter-routed call; `provider.allow_fallbacks: false` verified on 100% of dispatched calls via response-observed provider matching the pin.**

**P3 — Typography (4 wks)**
`pagescan` (Rust) · deterministic defect scan + bounded fixpoint optimizer · baseline grid · drop caps, ornaments, scene breaks · **8 designed templates, each merged only with typographer sign-off** · raster-diff regression harness · **chapter-parallel pagination + chapter-scoped cache (O3)**, scoped by what O1 left on the table.
**Gate.** Blind A/B vs a $900 human typesetter: ≥ 50 % prefer or cannot distinguish. Fixpoint proven terminating and monotonic. Raster diffs green across 8 templates × 3 profiles. Editing one chapter invalidates exactly one chapter's layout.
**Do not start P4 until this gate passes.** An agent papering over a weak scanner produces plausible fixes for defects you should have detected exactly, and you will never know which is which.

**P4 — Agent layer (3 wks)** *(new in v2)*
Agent runtime + tool surfaces · **Structure Wrangler** (proposals pre-populate the review UI) · **Compositor** with the crop-and-verify loop and programmatic pagemap scanning · **Preflight Explainer** · proposal/approval UI with rationale + before/after crops · task budgets, subagent caps, context editing.
**Gate.** Structure Wrangler ≥ 70 % of proposals accepted unedited. Structure review time drops from ~40 min to ≤ 5 min median on the corpus. **Zero agent-caused preflight failures. Zero AST writes.** Agent cost within the per-title budget.

**P5 — Scale & hardening (3 wks)**
Temporal migration · render-engine pool + admission control **sized to whichever engine won P0** · **ARM + Spot fleet split (O5), placement declared per stage** · autoscaling + backpressure · full sandbox tiering · security test suite (bombs, XXE, fuzz) · SBOM/signing/CVE gates · nightly reproducibility job · backup restore drill · DR runbook.
**Gate.** 1000-book soak: RSS flat, zero egress, p95 within budget, nightly rebuild byte-identical. Restore drill completed and timed. **≥ 80 % of batch CPU-seconds on Spot, with a forced-interruption test proving a killed stage re-runs to a byte-identical artifact.**

**P6 — Secondary outputs + the enterprise wedge (3 wks)**
IDML writer · EPUB 3 + a11y (EPUBCheck **and** ACE gates) · **`services/alttext`** with approval UI · ONIX 3.0 · large-print profile · Manuscript Doctor · Vendor Watcher · Support Triage with one-click repro bundle · **incremental EPUB** (same chapter-granularity argument as O3, far cheaper) · **Backlist Accessibility Triage (O6) packaged as a standalone product**.
**Gate.** IDML opens clean in InDesign, Affinity, Scribus. EPUB green in both validators. Unlicensed-font build fails as `policy_violation`. Support can reproduce any build from its manifest without engineering. **A 500-title backlist audit runs end to end and produces a ranked remediation plan for under $50 of compute.**

**P7 — Learning system (3 wks)** *(new in v2)*
L1 memory (title + tenant scopes, forgetting policy) · L2 nightly consolidation with validate/monitor/retire · promotion ladder + cross-tenant generalization gate · **judge calibration harness** · **holdout slice** · promotion CI with ratchet.
**Gate.** Judge agreement ≥ 0.90 per dimension against expert labels. Holdout slice live and reported. At least one pattern promoted end-to-end (correction → exemplar → mined rule) with zero golden-case regressions.

**P8 — Platform & API (3 wks)**
Public API + webhooks + idempotency · per-tenant quotas · billing · escape hatch shipped (IDML handoff and/or human-assist tier) · Prince adapter (paid tier) · Adobe InDesign API adapter behind `q.external` with spend ceiling and graceful degradation to Path A.
**Gate.** A third party integrates from the OpenAPI spec with no support contact.

**P9 — Breadth (ongoing)**
More templates · more vendors · the non-default renderer's template coverage · TeX for heavy STEM · Kindle · **backlist *remediation*** — the paid delivery that P6's triage product qualifies leads for · non-Latin scripts (explicit v1 exclusion, deliberate v2 project).

**Total to a sellable product: ~26 weeks sequential.** P0–P4 is the product; P5–P8 is the business; L3 distillation runs forever. **The optimizations do not extend this.** O1 and O4 are decisions and rule-set entries, not features; O2, O3, and O5 land inside phases that were already scoped for that work and reduce what P5 has to build; O6 is packaging of engineering P6 already contains.

### 5.2 Execution model — phases are narrative, tracks are causal

The phase order above is a reading order, not a build order. Most of it parallelizes, because the architecture was built for it: every stage is `f(inputs, params, toolchain) → artifacts` with **declared** schemas (§3.20, [ARCHITECTURE §2.8.1](ARCHITECTURE.md)), so stages are developable against fixtures with no knowledge of their neighbours.

**Three assumed dependencies that aren't real:** `prepress` needs *a* PDF, not *your* PDF (fixture PDFs, week 1 — worth ~3 weeks of critical path) · `structure` needs typescript HTML, not `ingest` (hand-write 20 fixtures in a day) · `cover` takes a page count as a parameter, not a real interior.

**Hard barriers — the only genuine serialization:**
```
W1  contracts frozen (schemas + stage registry) → all tracks unblocked
W3  tracer bullet green                         → fan-out permitted
W3  renderer decided (O1)                       → T4 may build one engine deeply;
                                                  O2 and O3 unblocked or dropped
W11 integration green                           → compliance gate
W11 pagescan done                               → agent track may start
W15 A/B gate passed                             → agents may touch composition
W19 soak + reproducibility green                → GA candidate
W21 pentest remediated                          → GA
```

**The renderer barrier is new in v2.1 and belongs at W3 with the tracer bullet.** It is not a barrier because the work is sequential — it's a barrier because T4, T5, and T6 all size their work against the answer, and the cost of them guessing wrong is three tracks of rework at W10 rather than two days of measurement at W2.

**Never fan out before the tracer bullet passes.** The failure mode of fixture-driven parallelism is five tracks building perfectly against contracts that turn out to be wrong. Weeks 2–3 are one thin, ugly, end-to-end slice through every stage whose only job is to validate the contracts.

**Coordination protocol** (cheap, and all four required): contract window — schema and registry changes land Mondays, batched, versioned, additive-only otherwise · fixture freeze — downstream pins a fixture version, regeneration is a PR · generated contract tests are the merge gate · weekly integration build from W4, red stops feature work.

**Single-owner artifacts, never split:** AST schema · cache key + stage contract · the three design emitters · preflight rules + vendor profiles · the typography fixpoint · the error taxonomy.

**Timeline by team:** 1–2 eng → 26 wks (parallelization doesn't help; reorder for risk instead) · 3 → ~20 · **5 + contract typographer → ~16** · 8+ → ~14 · floor ~13, set by feedback loops with external latency, not by code.

**The highest-value lever is not headcount.** Physical proof turnaround is 1–2 weeks *per cycle*; vendor submissions, typographer lead time, and pentest scheduling are the same shape. So: **start every slow loop in week 3 with a deliberately bad artifact, not in week 12 with a good one.** Track B (§5.0) exists for exactly this.

Full dependency graph, track structure, week-by-week schedule, and parallelization risks: [PARALLELIZATION.md](PARALLELIZATION.md).

### 5.3 Optimization integration ladder *(new in v2.1)*

The optimizations in [OPTIMIZATION.md](OPTIMIZATION.md) are not a backlog to work through when there's slack. They have a correct order, and it follows from three rules — applied in this precedence:

1. **Decisions before construction.** An optimization that changes what other people build is a gate, not a task. It runs as early as it can be measured, even if it is not the largest win.
2. **Build in, don't retrofit.** An optimization that is three days inside the phase that owns the code is three weeks after that phase ships, because retrofitting means re-touching a rule set, a schema, or a cache key that other work has since depended on.
3. **Then by payback, gated by dependency.** Only after 1 and 2 does raw impact decide the order.

Which produces:

| Order | Opt | Phase | Why *here* | Rule |
|---|---|---|---|---|
| **1** | **O1 Typst as default renderer** | **P0 gate (W2–3)** | Changes §3.9, §0's targets, P3's risk profile, and P5's infra budget. Cheap to measure now, expensive to reverse at W10 | 1 |
| **2** | **O8 Synthetic corpus expansion** | P0 → ongoing | Corpus licensing is on the critical path and can fail. Synthetic coverage is the hedge, and it must exist *before* it's needed, not after the gate slips | 1 |
| **3** | **O4 Two-artifact PDF + size budget** | P1 | 3 days *inside* the phase authoring the preflight rule set. After P1 it means reopening the rules, the profiles, and the artifact contract | 2 |
| **4** | **O5 ARM64 engine spike** | P1 (1 day) | The *spike*, not the migration. Images are being built in P1 anyway; the answer changes P5's fleet plan and can invalidate it | 2 |
| **5** | **O7 Prefix prewarming + global design cache** | P2 | Inside the gateway being built. Marginal alone; free here, awkward later | 2 |
| **6** | **O2 Client-side preview** | P2–P3 | Biggest UX win in the product. Blocked on O1; ships with the review UI so its view-tracking lands with the rest of the instrumentation | 3 |
| **7** | **O3 Chapter-parallel pagination + chapter cache** | P3 | Scope depends on what O1 left on the table. The cache-granularity half is worth doing even if the latency half isn't | 3 |
| **8** | **O5 ARM + Spot fleet split** | P5 | Hardening work, needs the spike's answer and a stable stage registry to declare placement against | 3 |
| **9** | **O6 Backlist triage** | P6 | Packaging, not engineering — P6 already builds EPUBCheck + ACE + batch inference. Promoted from a P9 footnote because it's a GTM motion, not a feature | 3 |

**Two of these are load-bearing beyond their own line.**

**O1 is the only entry that can make other entries disappear.** If Typst verifies, it deletes the largest quality risk in the register, most of the Chrome infrastructure in P5, and possibly O3's latency motivation. If it doesn't verify, the plan is exactly v2 and nothing is lost but two days. That asymmetry is why it sits at position 1 despite O2 arguably having a larger user-visible payoff.

**O8 is ranked far above its listed impact on purpose.** It appears in OPTIMIZATION.md as a second-order "quality, 1 wk, low risk" item. But golden-corpus licensing is a Track B external dependency with a real probability of slipping, and *every* correctness gate depends on it. Synthetic manuscripts that mimic real Word abuse are the only hedge that doesn't require someone else's signature. Build the generator early; discovering at P2 that you need it is discovering it too late.

**What is deliberately not sequenced here:** anything in OPTIMIZATION.md's *Explicitly rejected* table. A custom line-breaking engine, client-side final artifacts, multi-region active-active, and finer service splitting stay rejected, and this ladder is not a route by which they re-enter.

---

## 6. Risk register

| Risk | P | Impact | Mitigation |
|---|---|---|---|
| Quality ceiling — output visibly worse than a human | M | fatal | P3 gate is a blind A/B, not self-assessment. If it fails, add Prince/TeX before adding features |
| **No typographer on the team** | H | fatal | Track B week 1. Engineers ship engineer-looking books; the market reads it in three seconds |
| Chrome line-breaking is greedy single-line | H | quality | **Deleted, not mitigated, if O1 verifies** — Typst does real paragraph-level composition. The O1 verification is a P0 gate (§5.1), not a later experiment. Fixpoint optimizer + Prince/TeX escape remain the fallback if it doesn't |
| **O1 verification is ambiguous — Typst wins on cost, loses on a template** | M | schedule | Decide per-template, not globally: `preferred_engine` is already a DesignSpec field. The failure to avoid is a two-week re-run of the comparison because the first one wasn't scoped to a decision. Pre-commit to the metrics (SSIM, line-break agreement, wall-clock, peak RSS) and the thresholds **before** measuring |
| **Typst ecosystem gap found late** | M | quality | Chrome is kept as the compatibility path precisely for this, and the cross-emitter agreement gate (§3.8) keeps both honest. A single-engine bet on a young ecosystem is explicitly not taken |
| **Client preview diverges from the server build (O2)** | M | trust | Client engine version is in the toolchain digest and asserted on load; mismatch refuses to preview rather than showing a different book. Preview is never the deliverable |
| **Chapter-parallel pagination applied where cross-chapter flow exists (O3)** | M | wrong output | DesignSpec capability flag checked in the stage, not the UI; whole-book fallback when off. `stitch` treats a parity disagreement as `engine_bug`, never silently repairs it |
| **Spot interruption corrupts a build** | L | wrong output | Structurally prevented by content-addressing and idempotency, but *asserted* rather than assumed: P5's gate includes a forced-interruption test proving a killed stage re-runs byte-identical |
| **Golden corpus can't be licensed in time** | M | schedule | Track B week 1. Blocks every correctness gate; start before writing code. **Second, independent hedge: the O8 synthetic generator, built in P0** — the only corpus source that needs nobody else's signature (§5.3) |
| Font licensing exposure | M | legal | Enforced in the domain layer (§3.8), OFL-only default, attestation for uploads, per-build licence manifest |
| Engine or model upgrade silently changes output | H | trust | Toolchain digest (incl. learned state) + nightly byte-equality + raster diffs on upgrade PRs. Bots never merge these |
| Model mutates prose | L | fatal-to-trust | Closed-enum schema makes it structurally impossible; integrity gate is defence in depth |
| **Agent proposes something harmful** | M | quality | Action space is override ops only; every proposal gated; harm-rate tracked per agent; zero-AST-write test |
| **Feedback loop drifts undetected** | H | slow quality decay | Permanent ≥ 5 % holdout from agent assistance; unbiased baseline reported alongside every learning metric |
| **Cross-tenant style leakage via learning** | M | legal + quality | Generalization check at the tenant→global promotion gate |
| Adobe procurement blocks the roadmap | M | schedule | Path B never on the critical path; IDML export needs no Adobe |
| Memory blowups on large manuscripts | H | reliability | Streaming everywhere, declared budgets, admission control, 900-page regression fixture |
| Vendor spec drift (KDP/Ingram) | H | rejections | Profiles as versioned data; Vendor Watcher PRs; per-release real submissions |
| Ghostscript CVE | M | security | Pinned, strictest sandbox tier, no user PostScript |
| Cache correctness bug | M | wrong output | Stage-version CI rule + nightly cold-vs-cached byte comparison |
| LLM cost runs away at volume | L | margin | Rules-first, cascade, prompt caching, Batch API, per-tenant ceilings, cost regression test in CI |
| **Publisher refuses third-party model on manuscripts** | H | lost deals | `no_external_llm` routing flag + published subprocessor list + no-training tier, all ready before the first enterprise conversation |

---

## 7. Definition of done (every module)

- [ ] Public surface is generated types + `Result` returns; no exceptions cross the boundary
- [ ] Pure core has zero I/O, zero clock, zero randomness — enforced by a lint/import rule
- [ ] Property tests on invariants, not just examples
- [ ] Adversarial fixtures for anything touching untrusted input
- [ ] Declared memory budget with a regression fixture at the top of the range
- [ ] Stage `VERSION` bumped when behavior changed (CI-enforced)
- [ ] Metrics + trace spans emitted
- [ ] Failure modes mapped to the error taxonomy with correct retry policy
- [ ] Threat model paragraph in the module README
- [ ] Golden-corpus run green
- [ ] **If it calls a model**: goes through `services/inference`; schema has no free-text field unless it is `services/alttext`; refusal path tested; cost recorded
- [ ] **If it is an agent**: action space is override ops only; proposals carry rationale; zero-AST-write test present; per-agent eval set exists
- [ ] **If it learns**: learned state hashed into the toolchain digest; promotion gated by corpus CI with ratchet

---

## 8. Start here

Week 1, in order — plus Track B kicked off the same day.

1. **All artifact schemas** v0.1 — not just AST — + codegen both languages + CI sync gate. Contracts freeze Friday
2. `platform/stages` registry (§3.20): declaration decorator, DAG derivation, generated contract tests, `pub run-stage`
3. `platform/cas` with streaming hash and node-local cache
4. `cache_key()` + the stage contract + the Postgres DAG executor
5. `platform/sandbox` baseline (ro rootfs, no net, cgroups, seccomp) — **before the first parser runs**
6. First fixture sets per stage, corpus-derived where possible, hand-written where not
7. Golden corpus repo with the first 10 manuscripts and the raster-diff harness stubbed
8. **Synthetic corpus generator (O8)** — messy manuscripts that mimic real Word abuse. This is week 1, not P9, because it is the only corpus hedge that does not depend on someone else signing a licence (§5.3)
9. **Track B, same day**: corpus licensing outreach, typographer search, vendor accounts

Then weeks 2–3 are the **tracer bullet** — one thin ugly slice end-to-end — and only then does the team fan out.

**Decide the renderer inside the tracer bullet — it is a gate, not an experiment (O1).** Port two templates (one plain literary, one with sidebars/figures) to both `emit_css()` and `emit_typst()`; measure page-raster SSIM, line-break agreement, wall-clock, and peak RSS. **Write down the thresholds before you measure**, or the comparison becomes a debate. Typst is ~1/5 the time and ~1/10 the memory of headless Chrome and does real paragraph-level composition — if it holds on your templates it deletes the largest quality risk in the register and most of P5's render infrastructure. Two days now, three tracks of rework at W10 otherwise. See [OPTIMIZATION.md §O1](OPTIMIZATION.md) and §5.3.

Everything after that composes onto those nine. Get them right and the rest is additive; get them wrong and every phase pays interest.

---

## 9. The decisions that don't change

1. **Content-addressed build graph.** Not a queue of steps.
2. **AST derived and immutable; human *and agent* decisions in a separate rebasable override layer.**
3. **AST schema == ProseMirror schema == input to every writer.** One schema; editor, IDML, EPUB, CSS all fall out of it.
4. **Model output is frozen data, never a live decision** (D9). This is what lets a nondeterministic model and agent live inside a byte-reproducible pipeline.
5. **Geometry first, model second, human third.** In that order, always.
6. **Learning ends in deleted model calls.** Success is measured as inference removed, not intelligence added.

Everything else — Chrome vs Prince, Temporal vs Postgres, TS vs Python, which model tier — is swappable later. These six are not.
