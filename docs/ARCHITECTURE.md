# Publisher — Workflow & Software Architecture

Companion to [RESEARCH.md](RESEARCH.md). This doc answers two questions: **what happens, in what order** (workflow), and **how the system is built** (architecture).

**Status (E7.1, docs/ARCHITECTURE_SCORE_10_PLAN.md).** This document is normative: it
states the rules the code must hold to, and Parts 1–2 describe the pipeline and topology
as built, not as first envisioned. Three sections were rewritten because they described a
system that was never built (§2.3's `S3 / R2` box, all of §2.13, and §2.14's repo tree) —
those parts now moved to **[ARCHITECTURE_ROADMAP.md](ARCHITECTURE_ROADMAP.md)** with dates
and trigger conditions, and stay out of this document until built. §2.4's Temporal
discussion is the one place this doc deliberately keeps an aspirational design alongside
the shipped substitute — it says so explicitly, in place, rather than moving the discussion
out.

For the current, mechanically-current list of stages, folders and files, use
**`CLAUDE.md`'s folder map and the `.claude/skills/*`** — those are kept in sync with the
registry directly (see §2.14 below and `tools/lint_docs_claims.py`, E7.3). Part 1's stage
table below is the conceptual pipeline this system's design is built around; it does not
enumerate the literal current `@stage(...)` names 1:1 (some rows are folded together, e.g.
`structure-rules`/`structure-llm` are one `ast-assemble` gate today; some don't exist yet,
e.g. `sanitize`, `media-normalize`, `typo-optimize`, `rasterize`) — that literal list is
`stages/`, read via the **publisher-stages** skill.

---

## Part 0 — The one idea

> **A book build is a content-addressed build graph, not a script.**

Every stage is a pure function `f(inputs, params, toolchain) → artifacts`, keyed by a hash of all three. Nix/Bazel semantics applied to publishing.

Everything good falls out of this:

| Property | Falls out of |
|---|---|
| Change font → re-render in 6s not 90s | cache hit on ingest/infer, miss only downstream |
| Reprint identical PDF in 2029 | toolchain pinned by image digest in build manifest |
| A/B ten templates cheaply | same upstream artifacts, ten leaf nodes |
| LLM/Adobe-API cost control | inference is a cached node, not a call |
| Layout regression tests | page rasters are artifacts; diff them |
| Debuggable failures | every intermediate is retained and addressable |

Build it this way from commit 1. Retrofitting caching onto a procedural pipeline is a rewrite.

---

## Part 1 — The Workflow

### 1.1 User journey (what the customer sees)

```
 1. Create Title            metadata, ISBN, contributors, series
 2. Upload manuscript       .doc/.docx  (+ optional assets zip)
        ↓ 30–90s
 3. STRUCTURE REVIEW  ⟵ human gate #1
        chapter map, front/back matter, flagged ambiguities
        user reclassifies; decisions stored as overrides, AST untouched
 4. Design                  template + trim size + type pairing + ornaments
        live page preview, <10s round trip
 5. Output profiles         KDP 6×9 pb · IngramSpark 6×9 · EPUB 3 · IDML
 6. PROOF BUILD             page rasters + preflight report + watermarked PDF
 7. QUALITY REVIEW    ⟵ human gate #2
        widow/orphan/runt/river list, each with one-click fixes
        cover proof (spine now known)
 8. FINAL BUILD             blocked unless preflight = green
 9. Deliver                 PDF/X-1a interior · cover PDF · IDML · EPUB · ONIX 3.0
                            + signed compliance report
```

**Revision loop** (the real-world 80% case — author returns post-copyedit):
upload manuscript v2 → re-ingest → re-infer → **rebase overrides onto new AST via `sourceRef`** → show structural diff ("3 chapters added, ch.7 title changed, 2 overrides orphaned") → user resolves orphans only → rebuild. Never re-do the whole review.

### 1.2 System pipeline (what the machine does)

| # | Stage | In | Out | Engine | Cached | Typical |
|---|---|---|---|---|---|---|
| 1 | `acquire` | upload | `source.blob` + sha256 | api | n/a | <1s |
| 2 | `sanitize` | source | safe zip, manifest, threat report | py (zipguard) | ✅ | 1s |
| 3 | `legacy-convert` | `.doc` | `.docx` | LibreOffice headless | ✅ | 2–10s |
| 4 | `extract` | `.docx` | `typescript.html` + `media/*` + `notes.json` | Saxon/XSweet + docx4j | ✅ | 3–15s |
| 5 | `media-normalize` | media/* | sRGB/CMYK-safe PNG/JPG/SVG + ppi report | libvips, Inkscape (EMF/WMF) | ✅ | 2–20s |
| 6 | `structure-rules` | typescript.html | `ast.draft.json` + confidence | py | ✅ | 1s |
| 7 | `structure-llm` | draft + low-confidence nodes | label patches | Claude, structured out | ✅ | 5–20s |
| 8 | `ast-assemble` | draft + patches | **`ast.json`** (canonical) + integrity hash | py | ✅ | 1s |
| — | **HUMAN GATE 1** | ast + overrides UI | `overrides.json` | web | — | ∞ |
| 9 | `resolve` | ast + overrides | `doc.effective.json` | py | ✅ | <1s |
| 10 | `design-compile` | DesignSpec | `styles.css` **/** `styles.idml-part` **/** `styles.typ` | py (one spec → 3 emitters) | ✅ | <1s |
| 11 | `paginate` | doc + styles + profile | `raw.pdf` + `pagemap.json` | Playwright/CDP + Paged.js **/** Prince **/** Typst | ✅ | 20–90s |
| 12 | `typo-optimize` | pagemap | micro-adjust patch → re-`paginate` (bounded fixpoint) | py + engine | ✅ | 0–3 iters |
| 13 | `parity-pad` | pagemap | blank pages to ×4, final page count | py | ✅ | <1s |
| 14 | `finish` | raw.pdf + profile | CMYK, PDF/X-1a, OutputIntent, bleed, marks | Ghostscript + ICC | ✅ | 5–20s |
| 15 | `preflight` | final.pdf + profile | `preflight.json` **GATE** | py (pdfcpu/PyMuPDF rules) | ✅ | 3–10s |
| 16 | `rasterize` | final.pdf | page PNGs (preview + regression) | mutool/pdfium | ✅ | 5–15s |
| 17 | `cover` | **final page count** + cover art + profile | cover PDF/X | same render+finish path | ✅ | 10s |
| 18 | `epub` | doc.effective | `book.epub` + ACE + EPUBCheck reports | py | ✅ | 5s |
| 19 | `idml` | doc.effective + styles | `book.idml` (+ optional Adobe render) | SimpleIDML / own writer | ✅ | 5s |
| 20 | `onix` | Title metadata | `onix.xml` | py | ✅ | <1s |
| 21 | `package` | all artifacts | manifest + zip + signed report | api | n/a | 2s |

Cold full build, 300pp novel: **~90–150s**. Design tweak after cache warm: **~25s** (stages 10–16 only). Text edit of one chapter: stages 9–16.

### 1.3 Critical dependency edge people get wrong

```
interior build → final page count → spine width → cover geometry → cover build
```
Cover **cannot** be built before the interior converges. And `parity-pad` changes the page count, which changes the spine. Model it as a real DAG edge; do not let a UI let someone "design the cover first" and then silently mismatch.

### 1.4 The bounded fixpoint (stage 12)

```
compose → scan(pagemap) → { widows, orphans, runts, rivers, hyphen-stacks, short-chapter-ends }
  if clean or iter == MAX(3): stop
  else: emit deterministic micro-adjustments
        (paragraph tracking ±0.005em, hyphenation zone, keep-with-next,
         measure nudge on offending spread only)
        → recompose
```
Rules: deterministic (no RNG, no LLM), bounded, monotonic (never allowed to increase total defect score), and every adjustment recorded in the build manifest so a human can audit *why* page 143 has tighter tracking.

---

## Part 2 — Architecture

### 2.1 Principles

1. **Content-addressed, immutable artifacts.** Nothing is edited in place. Ever.
2. **Hexagonal.** Domain (AST, DesignSpec, PreflightRules, Profiles) has zero knowledge of Chrome, Ghostscript, Adobe, S3. Engines are adapters behind ports.
3. **Derived, never mutated.** `AST = f(manuscript)`. Human decisions live in a **separate override layer**. This is what makes revision-rebasing possible.
4. **Schema is the contract.** One JSON Schema per artifact type → codegen Pydantic + Zod. Polyglot workers can't drift.
5. **Workers are stateless and hostile-input-hardened.** No egress, read-only rootfs, hard caps, killed on timeout.
6. **Every gate is explicit.** Preflight failure blocks delivery. No "warning, shipped anyway".
7. **Reproducible by construction.** Pin images by digest, fonts by hash, ICC by hash, dictionaries by version. Record all of it.
8. **Stages are declared, not wired.** A stage registers its input schemas, output schemas, version, toolchain, and fixture set *as data*. The DAG, the contract tests, and the local dev harness are all **derived** from that declaration — nobody hand-edits orchestration to add a stage. This is what turns "parallelizable in principle" into "five people build six stages simultaneously without talking." See §2.8.1 and [PARALLELIZATION.md](PARALLELIZATION.md).

### 2.2 Bounded contexts

```
identity      tenants, users, orgs, entitlements, billing
library       Title, Manuscript, contributors, ISBN, ONIX metadata
structure     extraction, inference, AST, overrides, revision rebase
design        DesignSpec, templates, tokens, style compilers, font vault + licensing
composition   renderers (html/idml/tex), pagination, typography optimizer
prepress      color, PDF/X, geometry, imposition-lite, preflight rules
delivery      packaging, vendor profiles, distribution adapters, reports
platform      build graph, cache, artifact store, orchestration, sandboxing, telemetry
```

Dependency direction: `delivery → prepress → composition → design → structure → library → identity`, all sitting on `platform`. Nothing points back inward.

### 2.3 Physical topology

```
                    ┌──────────────┐
   browser ────────▶│  Web (Next)  │
                    └──────┬───────┘
                           │ REST + SSE
                    ┌──────▼───────┐        ┌─────────────┐
                    │  API (TS)    │───────▶│  Postgres   │  metadata, cache index,
                    │  Fastify     │        └─────────────┘  overrides, audit
                    └──────┬───────┘
                           │ start/signal/query
                    ┌──────▼────────────────┐
                    │  Temporal             │  durable workflow state
                    └──────┬────────────────┘
        ┌──────────────────┼──────────────────┬─────────────────┐
        │ q.ingest         │ q.render.html    │ q.prepress      │ q.external
   ┌────▼─────┐      ┌─────▼──────┐     ┌─────▼─────┐    ┌──────▼──────┐
   │ ingest   │      │ chrome     │     │ gs + pre  │    │ adobe-api   │
   │ LO+Saxon │      │ Playwright │     │ flight    │    │ token bucket│
   │ (py)     │      │ Paged.js   │     │ (py)      │    │ (ts)        │
   └────┬─────┘      └─────┬──────┘     └─────┬─────┘    └──────┬──────┘
        └──────────────────┴──────────────────┴─────────────────┘
                           │
                    ┌──────▼───────┐
                    │  S3 / R2     │  content-addressed artifacts (immutable)
                    └──────────────┘
```

Separate queues per **capability**, not per stage. Autoscale each independently — Chrome pods are expensive and warm, Ghostscript pods are cheap and cold, Adobe calls are rate-limited and metered.

**As built today**, this is the target topology this repo's shape is built toward, not the
deployed one: one `worker.py` process (N horizontally-scaled copies, `docker-compose.yml`'s
`worker` service) claims from Postgres with `FOR UPDATE SKIP LOCKED` — see §2.4's own
documented Temporal substitute — and runs every stage in-process rather than dispatching to
per-capability pods; there is no Chrome/Playwright renderer (weasyprint or Typst, selected by
`PUBLISHER_RENDER_ENGINE`) and no `adobe-api` queue. Artifacts live in a local
content-addressed store (`platform/cas/`, `PUBLISHER_CAS_ROOT`), not `S3 / R2` — see §2.13.
The worker's egress is `internal: true` (no route to the internet) except for
`infra/llm-egress/`'s allowlist proxy, added when `structure-infer` needed one real external
call (OpenRouter); the S3/R2 tier is `ARCHITECTURE_ROADMAP.md`'s **R3**.

Per-capability dispatch itself is no longer absent, but it is **not the default**: an
opt-in `docker compose --profile temporal` path (`worker_temporal.py`,
`platform/orchestration/`) runs each stage as a Temporal activity on the queue its own
`@stage(...)` declares, so a render-only pool can be scaled independently. A plain
`docker compose up -d` starts the single-`worker` topology described above and nothing
else. The autoscaling policy, a production Temporal cluster, and workflow versioning are
still missing — see `ARCHITECTURE_ROADMAP.md`'s **R1** for exactly what landed.

### 2.4 Why Temporal (and not Celery/BullMQ/Step Functions)

The workflow has: multi-minute duration, **indefinite human pauses**, fan-out/fan-in, per-stage retry policies, versioned logic that must not break in-flight builds, and a hard reproducibility requirement.

```ts
export async function bookBuild(input: BuildInput): Promise<BuildResult> {
  const src   = await act.acquire(input.uploadRef);
  const safe  = await act.sanitize(src);
  const docx  = safe.isLegacy ? await act.legacyConvert(safe) : safe;

  const [typescript, media] = await Promise.all([
    act.extract(docx),
    act.mediaNormalize(docx),
  ]);

  const draft = await act.structureRules(typescript);
  const ast   = await act.astAssemble(
    draft,
    draft.lowConfidence.length ? await act.structureLlm(draft) : null,
  );

  // ── human gate: durable, survives deploys, no polling ──
  setHandler(applyOverrides, (o) => { overrides = o; });
  await condition(() => overrides !== null);   // days, if the author goes on holiday

  let build = await composeAndFinish(ast, overrides, input.design, input.profile);

  // cover depends on the interior's *final* page count
  const cover = await act.cover({ pages: build.pageCount, art: input.coverArt });

  return act.package([build, cover, ...await fanOutSecondary(ast, overrides)]);
}
```

Workflow code must be deterministic (Temporal replays it). All I/O lives in activities. Version with `patched()` so a build started yesterday finishes on yesterday's logic.

If Temporal is too much operational weight for v1: **Postgres-backed DAG executor + advisory locks** is an acceptable 300-line substitute. Do *not* use a plain job queue — you'll rebuild dependency tracking badly.

### 2.5 The build graph & cache key

```python
def cache_key(stage: str, version: str, inputs: list[Sha256],
              params: dict, toolchain: ToolchainDigest) -> Sha256:
    return sha256(canonical_json({
        "stage": stage,              # "paginate"
        "version": version,          # bump on ANY behavior change
        "inputs": sorted(inputs),    # content hashes of every input artifact
        "params": params,            # canonicalized: sorted keys, no floats-as-strings
        "toolchain": toolchain,      # image digest + font-set hash + icc hash + dict version
    }))
```

Rules that make this actually work:

- **`version` is a human-maintained integer per stage.** Any logic change bumps it. Enforce in CI: if `stages/paginate/**` changed and `VERSION` didn't, fail the build.
- **Toolchain digest is not optional.** `sha256(image_digest ‖ fontset_hash ‖ icc_hash ‖ hyphen_dict_version ‖ engine_semver)`. A Chrome minor bump changes line breaking → must invalidate.
- **Params must be canonical.** Sort keys, fixed float formatting, drop nulls. Otherwise you get cache misses that look like nondeterminism.
- **Nondeterministic bytes get stripped.** PDF `/ID`, `/CreationDate`, `/ModDate` → set to fixed values derived from the cache key. Then the *output* is content-addressable too, and you can assert byte-equality in tests.

Cache index: one Postgres table, `(cache_key PK, artifact_refs jsonb, created_at, hits, bytes)`. GC by LRU + minimum retention for any build referenced by a delivered order.

### 2.6 Domain model

```
Tenant ─┬─ User
        └─ Title ─┬─ Manuscript(v1, v2, …)      immutable uploads
                  ├─ Document(ast, from Manuscript × InferenceVersion)   immutable
                  ├─ OverrideSet(per Document lineage)  mutable, rebasable
                  ├─ DesignSpec(v)               immutable, forkable
                  ├─ OutputProfile(vendor+trim+pdfx) immutable, versioned
                  └─ Build(Document × OverrideSet × DesignSpec × Profile × Toolchain)
                        └─ Artifact[]  (content-addressed, immutable)
```

Two things to get right:

**(a) Overrides are addressed by `sourceRef`, not by index.**
```json
{ "sourceRef": "docx:body/p[412]#h3a91c",   // stable-ish id + content hash
  "op": "reclassify",
  "from": "heading:2", "to": "chapter-title",
  "actor": "user:8812", "at": "2026-07-29T10:02:11Z" }
```
On re-ingest, rebase by exact `sourceRef` → fall back to content-hash match → fall back to fuzzy text match → else mark **orphaned** and surface to the user. Never silently drop a human decision.

**(b) The AST node schema *is* the ProseMirror schema.**
One schema serves: validation, the editor (ProseMirror/Wax), the IDML writer, the EPUB writer, the CSS projection. No mapping layer, no drift. This is the single highest-leverage schema decision in the project.

```jsonc
// ast.schema.json (excerpt)
{
  "type": "chapter",
  "attrs": { "number": 7, "title": "The Long Way Down", "startsOn": "recto", "id": "ch7" },
  "content": [
    { "type": "paragraph", "attrs": { "role": "chapter-opening" },
      "content": [ { "type": "text", "text": "It began…" } ] },
    { "type": "sceneBreak", "attrs": { "ornament": "dinkus" } },
    { "type": "blockquote", "attrs": { "role": "epigraph", "source": "…" }, "content": [...] }
  ]
}
```

### 2.7 One DesignSpec → three style compilers

```
DesignSpec (tokens + rules, engine-agnostic)
   │  measure, leading grid, scale ratio, margins by pagecount,
   │  font stack + optical sizes, ornaments, chapter-opening treatment,
   │  running-head policy, folio policy, hyphenation params
   ├──▶ emit_css()    → @page rules, counters, string-set, footnote areas
   ├──▶ emit_idml()   → ParagraphStyle / CharacterStyle / ObjectStyle XML parts
   └──▶ emit_typst()  → #set / #show rules
```

Non-negotiable: **the DesignSpec is the only source of typographic truth.** No renderer may carry its own defaults. Contract test: for a fixed corpus, all three emitters must agree on page count ±2% and identical line-break decisions on ≥95% of paragraphs. When they diverge, that's a bug in an emitter, not "engines differ".

### 2.8 Stage contract & error taxonomy

```python
class StageResult(BaseModel):
    artifacts: list[ArtifactRef]
    metrics:   dict[str, float]      # duration, pages, bytes, defect counts
    warnings:  list[Diagnostic]
    toolchain: ToolchainDigest

class StageError(Exception):
    kind: Literal[
        "bad_input",       # user's fault → actionable message, no retry, no page
        "policy_violation",# preflight gate, license missing → hard stop
        "engine_bug",      # our fault → capture full input bundle, page, no retry
        "infra",           # OOM/timeout/network → retry w/ backoff, then escalate
        "external_limit",  # Adobe/LLM 429 → retry with jitter, respect Retry-After
    ]
    diagnostics: list[Diagnostic]     # each: severity, code, sourceRef, humanMessage, fix?
```

Every user-facing error carries a `sourceRef` so the UI can jump to the offending paragraph. "Conversion failed" is banned.

Retry policy per kind — `infra` 5× exponential, `external_limit` honours `Retry-After`, `bad_input`/`engine_bug`/`policy_violation` never retry.

#### 2.8.1 The stage registry — declaration, not wiring

A stage contract that lives only in prose is a convention. A stage contract that lives in **data** is a mechanism. Every stage registers itself:

```python
@stage(
    name="finish",
    version=9,                                  # bump on any behavior change (CI-enforced)
    inputs={"pdf": "raw-pdf/1", "profile": "profile/1"},
    outputs={"pdf": "pdfx/1", "report": "finish-report/1"},
    toolchain=["ghostscript", "icc"],           # resolves to digests at build time
    fixtures="fixtures/finish/v3",              # canonical inputs + expected outputs
    memory_budget_mb=512,
    queue="q.prepress",
)
def finish(ctx: StageCtx, pdf: ArtifactRef, profile: OutputProfile) -> StageResult: ...
```

Five things are **derived** from that declaration rather than hand-written:

| Derived | How | What it buys |
|---|---|---|
| **The DAG** | Match each stage's declared `inputs` to other stages' `outputs` | Adding a stage never touches orchestration code. T5 ships `finish` and `preflight` without T1 editing the executor |
| **Contract tests** | Run the stage over its fixture set; assert outputs validate against declared output schemas | Zero hand-written contract tests; a track physically cannot merge a stage whose output drifted |
| **Cache key inputs** | `name + version + input hashes + canonical params + toolchain digest` | The key is computed, never assembled by hand — no stage can forget a cache input |
| **Local dev harness** | `pub run-stage finish --fixture v3` | Any engineer runs any stage in isolation, reproducibly, with no other service running |
| **Admission control** | `memory_budget_mb`, `queue` | Scheduler needs no per-stage special-casing |

**Fixtures are first-class artifacts, not test files.** They live in CAS like everything else, are versioned, and are derived from the golden corpus:

```
fixtures/<stage>/<version>/
  inputs/          canonical inputs, content-addressed
  expected/        expected outputs (schema-valid; not byte-golden except where reproducibility demands it)
  manifest.json    provenance: which corpus manuscripts, which toolchain, when generated
```

Downstream tracks **pin a fixture version** and upgrade deliberately. A track that regenerates fixtures silently breaks four other people, so regeneration is a PR like any other.

**Why this is architectural and not process.** Every stage's neighbours are reachable only through declared, versioned, content-addressed artifacts. There is no shared memory, no in-process call, no import across stage boundaries. That is exactly the property that lets six stages be built simultaneously against fixtures by people who never talk — and the registry is what makes it mechanically enforced instead of politely agreed.

The one thing the registry cannot derive is **whether the contract is right**. That's what the tracer bullet is for (see [PARALLELIZATION.md](PARALLELIZATION.md) §2): one thin end-to-end slice through every stage before anyone fans out.

### 2.9 Worker sandbox (input is hostile by definition)

A `.docx` is a zip of XML from a stranger.

| Threat | Control |
|---|---|
| Zip bomb | cap entries (10k), uncompressed total (2 GB), compression ratio (100:1), depth |
| XXE / billion laughs | disable DTD + external entities in Saxon, lxml, every parser. Assert in tests. |
| Path traversal in zip | reject any entry with `..` or absolute path; extract via allowlist |
| VBA / OLE payload | drop `vbaProject.bin`, `oleObject*`, never hand to LibreOffice with macros enabled |
| Image decompression bomb | libvips with pixel cap; re-encode everything; strip EXIF/ICC-unless-valid |
| LibreOffice hang | per-job `-env:UserInstallation`, wall-clock kill, no profile reuse |
| SSRF from HTML/CSS | **no network in render workers**; all assets pre-resolved to local paths |
| Chrome escape | real sandbox on (never `--no-sandbox`); gVisor or Firecracker for the pool |
| Font as attack surface | render fonts only from the vetted vault; hash-verified; FontTools sanitize on tenant upload |

Baseline for every worker: read-only rootfs · tmpfs `/work` · no egress (egress only from the `q.external` worker) · seccomp · memory + CPU quota · wall-clock kill · non-root · dropped capabilities.

### 2.10 Font vault & licensing enforcement (this is architecture, not legal boilerplate)

```
FontAsset { hash, family, style, source: BUNDLED_OFL | TENANT_UPLOAD | LICENSED_SERVER,
            licenseRef, allowedUses: [PRINT_PDF, EPUB_EMBED, SERVER_RENDER],
            attestationBy, attestationAt }
```
`design-compile` **refuses** to emit a spec referencing a font whose `allowedUses` don't cover the target output. Enforced in the domain layer, not the UI. A build that would embed an unlicensed face fails as `policy_violation` with a named remedy. This keeps you out of court and is three days of work.

### 2.11 Reproducibility contract

Every `Build` row stores:
```json
{ "images":   { "ingest": "sha256:…", "chrome": "sha256:…", "gs": "sha256:…" },
  "fonts":    "sha256:…",           // hash of the resolved font set
  "icc":      { "cmyk": "sha256:…", "rgb": "sha256:…" },
  "hyphen":   { "en-US": "hyph-en-us-2024.1" },
  "engines":  { "pagedjs": "0.4.3", "gs": "10.04.0", "chrome": "141.0.7390.54" },
  "schema":   "ast/3.2.0",
  "stageVersions": { "paginate": 17, "finish": 9, "preflight": 22 }
}
```
Rebuild = restore the manifest, re-pull by digest, assert output hash equality. CI runs this nightly against the golden corpus; drift is a P1.

### 2.12 API surface

```
POST   /v1/titles
POST   /v1/titles/:id/manuscripts          multipart | presigned-PUT   Idempotency-Key
GET    /v1/manuscripts/:id/structure        → AST projection + confidences
PATCH  /v1/documents/:id/overrides          → override ops (CRDT-ish, append-only)
POST   /v1/documents/:id/rebase             → after new manuscript version
GET    /v1/design/templates
POST   /v1/titles/:id/design                → DesignSpec (fork/patch)
POST   /v1/builds        { documentId, designId, profileIds[], mode: proof|final }
GET    /v1/builds/:id                       → status + stage graph + metrics
GET    /v1/builds/:id/events                SSE: per-stage progress
GET    /v1/builds/:id/preflight             → structured report
GET    /v1/builds/:id/artifacts/:kind       → presigned download
POST   /v1/webhooks                         build.completed | build.failed | gate.awaiting
```

API-first from day one. The web UI is the first API consumer, not a privileged one. That's what makes the publisher/service-bureau segment (the actual money) addressable.

### 2.13 Storage layout

**As built:** one local content-addressed store per deployment, `platform/cas/`
(`ContentAddressedStore`), rooted at `PUBLISHER_CAS_ROOT` (default `./.publisher/cas`,
`/data/cas` in `docker-compose.yml`'s named volume):

```
<PUBLISHER_CAS_ROOT>/<sha256[0:2]>/<sha256[2:4]>/<sha256>   immutable, content-addressed
```

Every artifact kind — manuscript uploads, `raw-source/1`, `pdfx/1`, `epub/1`, fixtures,
everything a stage produces — is one blob in this one store, sharded by hash. There is no
tenant-prefixed path and no separate uploads/exports/fixtures namespace: a manuscript's
bytes are found via `manuscripts.source_sha256` (Postgres), not a storage path, and
`packages/api`'s artifact-download route resolves a build's deliverable the same way
(`db.ts`'s `DELIVERABLE_SCHEMAS` → `artifacts.schema_id` → CAS hash). This already gives
fixtures the property the original design wanted from S3: "run this stage against fixture
v3" and "run this stage against last Tuesday's real build" are the same code path, because
both are just a hash in the same store.

An S3/R2-backed `CasBackend` (multi-host deployment, or the local volume becoming the
bottleneck), per-tenant encrypted upload storage, and an Adobe InDesign API hand-off surface
(presigned URLs, 2 GB cap) are `ARCHITECTURE_ROADMAP.md`'s **R3** — not built, and the CAS
abstraction is already the right seam for it: swapping the backend is an adapter, not a
rewrite.

### 2.14 Repo layout (polyglot monorepo)

**As built** (`tools/lint_docs_claims.py` checks every path below still exists, E7.3 —
this is generated-checked, not hand-maintained-and-trusted):

```
publisher/
├─ schemas/                     # SOURCE OF TRUTH — JSON Schema
│   ├─ ast/ overrides/ designspec/ profile/ preflight/ manifest/
│   ├─ pagemap/ classification/ agent-proposal/ cover/
│   └─ codegen/                 → py (pydantic) + ts (zod); .gen.* is gitignored, regenerate
├─ stages/                      # STAGE REGISTRY (§2.8.1) — 24 @stage(...) declarations
│   ├─ *_stage.py | prepress_stages.py | cover_stages.py | secondary_output_stages.py | typst_stages.py
│   │                           → DAG, contract tests, cache keys, dev harness all derived
│   ├─ rendering.py, media.py   # shared, non-stage helpers stages import
│   └─ tests/
├─ fixtures/                    # per-stage fixture sets: ast/ cover/ cover-brief/
│   │                             design-compile/ extract/ finish/ finish-gs/
│   │                             manuscripts/ paginate/ preflight/ structure/
│   └─ <stage>/<version>/{inputs,expected,manifest.json}
├─ packages/
│   ├─ api/            (ts)     Fastify, auth (E5), tenancy (E4), idempotency, SSE
│   └─ web/            (ts)     Next.js: structure review UI (type-checked only, E0.4)
├─ services/
│   ├─ ingest/         (py)     DOCX → AST (`docx_to_ast`), legacy .doc via LibreOffice
│   ├─ structure/      (py)     rules, InferenceGateway/OpenRouterProvider, overrides
│   ├─ prepress/       (py)     geometry, fontvault, Ghostscript, preflight
│   ├─ cover/          (py)     art brief, model policy, image-gen port, judge
│   ├─ epub/ onix/ alttext/ (py) wired as `epub`/`onix`/`manuscript-advisory` stages (E7.2)
│   ├─ idml/           (py)     IDML writer, opt-in `idml` stage
│   └─ agents/         (py)     structure_wrangler, compositor, preflight_explainer
├─ platform/
│   ├─ cas/  cache/  stages/  exec/  sandbox/     (py + ts/rs where noted)
│   ├─ db/             SQL migrations (E4.1) + schema.sql
│   ├─ routing/        policy.yaml — LLM route table
│   ├─ pagescan/       (rs)     typographic defect scanner
│   ├─ reproducibility/  supply-chain/
├─ profiles/                    # vendor specs as versioned YAML: kdp/ ingramspark/ lulu/ generic/ greek/
├─ templates/                   # DesignSpec presets (one module, not per-template files)
├─ corpus/                      # generator/ manuscripts/ raster-diff/
├─ tests/                       # contracts/ integration/ meta/ + collection-floor guard
├─ tools/                       # CI-blocking lints (schema, stage-version, service-deps,
│                                 tenant-scoping, docs-claims)
├─ scripts/                     # test.ps1 / test.sh — required test runners
├─ infra/
│   └─ llm-egress/     tinyproxy allowlist proxy (E3.1/E6.2) — the ONE thing under infra/
│                       actually built; k8s/terraform per-engine images are not (§0.1, roadmap)
└─ docs/                        # this file + BUILD_PLAN, the *_PLAN remediation docs, ROADMAP
```

`packages/orchestrator`, `packages/worker-render`, and `services/design` do not exist —
see `ARCHITECTURE_ROADMAP.md`.

Language split rationale: TS where the ecosystem is Node-shaped (the API, the review UI); Python where the document libraries live (lxml, python-docx, weasyprint, fonttools) and where the pipeline itself runs; Rust for two small hot/pure cores (`platform/cas`'s hashing, `platform/pagescan`'s defect scanner). The JSON Schema codegen is what stops the Python/TS halves from drifting.

### 2.15 Testing

| Layer | Method | Gate |
|---|---|---|
| Schema | round-trip + codegen-in-sync check | CI blocking |
| **Stage contract** | **generated from the registry**: run each stage over its fixture set, assert outputs validate against declared output schemas | **CI blocking, zero hand-written** |
| **DAG integrity** | every declared input schema is some stage's declared output (or a root artifact); no orphans, no cycles | CI blocking |
| Extraction | 50-manuscript golden corpus → AST snapshots | diff review required |
| Integrity | `normalize(text(ast)) == normalize(text(docx))` | **hard fail, always** |
| Inference | labeled corpus, precision/recall per node type | ≥0.95 chapter-title recall |
| Design emitters | cross-engine agreement (page count ±2%, line breaks ≥95%) | blocking |
| Composition | page-raster SSIM vs golden per template × profile | ≥0.995 |
| Prepress | preflight rules as property tests + synthetic bad PDFs | blocking |
| Reproducibility | nightly rebuild-from-manifest, assert byte equality | P1 on drift |
| Vendor | real submission to IngramSpark/KDP sandbox each release | manual, per release |
| Security | zip bombs, XXE, malformed OOXML, font fuzz corpus | blocking |

### 2.16 Scale & cost shape

- Cost per book, Path A: **cents** (Chrome CPU-seconds + GS + storage). LLM inference ~$0.02–0.10 if you only send low-confidence nodes — never the whole manuscript.
- Chrome is the bottleneck: keep a warm pool, reuse browser contexts, one book per context, recycle after N books (memory creep is real).
- Adobe API path is metered + rate-limited → own queue, per-tenant token bucket, and a hard monthly spend ceiling with graceful degradation to Path A.
- 300pp book ≈ 40–80 MB of retained artifacts at proof quality. GC aggressively; retain forever only what's referenced by a delivered order.

### 2.17 Build order for the architecture itself

| Ship now | Defer | Never |
|---|---|---|
| CAS + cache key + stage contract | Temporal (start with PG DAG executor) | mutable AST |
| JSON Schema + codegen | Prince adapter | renderer-local style defaults |
| Sandbox baseline | IDML writer | LLM touching prose |
| Preflight gate | Adobe API adapter | shipping on preflight warnings |
| Override layer + rebase | Typst/TeX renderer | unpinned toolchains |
| Golden corpus + raster diffing | multi-region | per-customer forked pipelines |

---

## Part 3 — The four decisions that matter

1. **Content-addressed build graph.** Not a queue of steps. Everything else depends on this.
2. **AST is derived and immutable; human *and agent* decisions are a separate rebasable override layer.** This is what makes the revision loop — the actual daily workflow — not miserable, and it is what lets a nondeterministic agent live inside a reproducible pipeline.
3. **AST schema == ProseMirror schema == the input to every writer.** One schema; editor, IDML, EPUB, CSS all fall out of it. No mapping layer to rot.
4. **Stages are declared, not wired** (§2.8.1). The DAG, the contract tests, the cache keys, and the dev harness are derived from stage declarations. This is what makes the system buildable by several people at once instead of one person in sequence — an org-chart property that only the architecture can grant.

Everything else — Chrome vs Prince, Temporal vs Postgres, TS vs Python, which model tier — is swappable later. These four are not.
