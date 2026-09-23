# Remaining work

**Status:** survey, 2026-09-23, against `23adc58`.
**Method:** every item below was checked against the code in this tree, not recalled from
a plan document. Each carries a `file:line` you can open. Where a claim is "nothing calls
X", it means a repo-wide grep excluding `node_modules/`, `.next/` and `.claude/worktrees/`
returned only comments and tests.

The repo's plan documents (`BUILD_PLAN.md`, `ARCHITECTURE_SCORE_10_PLAN.md`,
`ARCHITECTURE_UPLIFT_PLAN.md`) describe what was *intended*. This file describes what is
*absent*, which is not the same list — several planned items landed, and several things
nothing planned are missing.

---

## The shape of what's left

The pipeline's spine is real: DOCX → AST → effective doc → render → PDF/X → preflight →
package runs end to end, with two hard gates (text integrity, preflight) that cannot be
bypassed and a third (composition) added in `65f5834`.

Almost everything remaining falls into one of three shapes:

1. **Built and disconnected** — a component exists, is tested, and nothing calls it.
2. **Measured and discarded** — a value is computed and then dropped before anything reads it.
3. **Never built** — an honest absence.

Shape 2 is the dangerous one. It is the same defect class as the composition bug fixed in
`65f5834` and the agent-tool bug fixed in `23adc58`: a path that *looks* like it measures
something, produces a clean-looking result, and certifies nothing.

---

## Tier 1 — broken loops (built, tested, unreachable)

These are the highest-value items: the code already exists, so the remaining work is
wiring, not invention.

### 1.1 The human review loop does not close

The "first human gate" is three disconnected pieces.

| Piece | Where | State |
|---|---|---|
| Review UI | `packages/web/src/review/StructureReviewPanel.tsx:23` | Exported, **never imported**. `packages/web` has no `src/app/` or `src/pages/` — there is no Next.js route that mounts it. |
| Override API | `packages/api/src/routes/manuscripts.ts:208` | Accepts `PATCH /v1/documents/:id/overrides`, validates the body, checks tenancy — then **discards `ops`** and returns `{applied: ops.length}` (`:229`). Nothing is persisted. |
| Override consumer | `stages/resolve_stage.py:38` | `resolve` takes `overrides_path` as an **optional root input**. Nothing in `packages/api/src/routes/builds.ts` ever supplies one (grep for `override` in that file returns zero hits). |

So: the panel cannot be opened, the endpoint that would receive its edits throws them
away, and even if it stored them there is no path from storage to a build's root input.
`services/structure/publisher_structure/overrides.py` — the one piece that actually
works, and now refuses ops it cannot apply (`dc6a6f3`) — is reachable only from
`tracer_bullet.py` and tests.

**Work:** persist ops on PATCH; add an overrides root input to build submission; add one
Next.js route that renders the panel. Smallest honest version is the API half — a stored,
buildable override log is useful without a UI.

### 1.2 Nothing dispatches the agent tools

`services/agents/publisher_agents/runtime.py:286` calls `_run_agent_logic`, which at
`:305` branches on role and computes its output **directly from `inputs`**. The
`tools=[...]` argument is used only for a length check against `budget.max_turns` and
recorded on `AgentCall.tools_used:58`. `registry.call` is never reached from `execute`.

`23adc58` made the five tools answer honestly from artifacts they are handed. It did not
make them reachable. Separately, **no stage constructs any agent class** — grep for
`StructureWrangler|Compositor|PreflightExplainer|AgentRuntime` across `stages/` returns
nothing. The agents package is dead from both ends.

**Work:** turn `_run_agent_logic` into a real tool-dispatch loop (the docstring at `:311`
says this is where the LLM call belongs), and decide whether an `agent-propose` stage
exists at all. Large. Do not start it as a wiring task — it is a design task.

### 1.3 LLM classification is computed and thrown away

`stages/structure_infer_stage.py:47` declares `terminal_outputs=["classification"]`.
The stage makes a real OpenRouter call, parses a real response, writes a `classification/1`
artifact — and **nothing consumes it**. The stage's own docstring (`:18`) calls it
"advisory input", which is accurate and also the problem: nothing takes the advice.

Worse, upstream: `services/structure/publisher_structure/rules.py` computes a per-block
`confidence` (`:349`, `:360`, `:373`…), but `stages/structure_stage.py` (`ast-assemble`)
**never writes confidence into the AST** — grep for `confidence` in that file returns zero
hits. Consequences, all verified:

- All six `corpus/manuscripts/*.ast.json` contain zero `confidence` fields.
- `packages/api/src/routes/manuscripts.ts:193` hardcodes `confidence: 1.0` for every chapter.
- `manuscripts.ts:182` and `:200` hardcode `lowConfidenceNodes: []`.

That last pair is exactly the shape-2 defect: the review UI would be told *every* book has
no uncertain nodes, because nothing ever recorded one. Same collapse as the composition
path before `65f5834`.

**Work:** carry `confidence` from `rules.classify_blocks` into the AST nodes
`ast-assemble` emits; populate `lowConfidenceNodes` from it in the API; let something
consume `classification/1` (probably `resolve`, as proposed overrides). The first step is
small and unblocks the other two.

### 1.4 Both Rust crates are unreachable from Python

`platform/pagescan/src/lib.rs:442` says PyO3 exports live "in a separate file
(`lib_py.rs`)". **That file does not exist** — `platform/pagescan/src/` contains only
`lib.rs`. `Cargo.toml` has no `pyo3` dependency and no `pyo3` feature.

`platform/cas/` is the same story from the other side: `platform/cas/src/lib.rs` is a Rust
implementation, `platform/cas/py/publisher_cas/` is a **separate pure-Python
implementation** (`hashlib`, `:10`), and `platform/cas/ts/` is a third. Nothing in
`packages/` imports the TS one. Three implementations of one content-addressed store, with
no cross-implementation agreement test.

CI does run `cargo test` and `cargo clippy -- -D warnings` (`ci.yml:119-120`), so the Rust
is correct — it is just not on any path.

**Work:** either build the PyO3 binding and delete the duplicated logic, or delete the
unreachable crates and the comment that promises a file that was never written. The second
is smaller and more honest. If the crates stay, a cross-implementation test (same input,
same digest / same defects) is the minimum.

---

## Tier 2 — measurement gaps (a gate exists but cannot see)

### 2.1 Rivers and hyphen stacks are no-ops

`platform/pagescan/src/lib.rs:246` (`detect_rivers`) and `:253` (`detect_hyphen_stacks`)
take their arguments, discard them via `let _ = page;`, and push no defects. Both are
called from `scan()` at `:137-138`.

This is *correctly* deferred, not a bug: both genuinely need glyph positions from a
rendered raster, which nothing in this repo produces. It is listed here so nobody reads
`scan()`'s call list and concludes these are covered. Note it is also currently moot —
per 1.4, nothing calls `scan()` from Python at all.

**Work:** blocked on a page-raster pipeline. Do not attempt from pagemap hints; a
heuristic river detector that fires on layout metadata is worse than none, because it
would produce a `false` where the truth is "unknown".

### 2.2 Missing preflight checks

`services/prepress/publisher_prepress/preflight.py` implements eleven checks: trim size
(`:115`), bleed (`:150`), min/max pages (`:175`, `:200`), page multiple (`:225`), colour
space (`:251`), resolution (`:268`), file size (`:296`), font embedding (`:317`), PDF
standard (`:335`), composition (`:414`).

Not implemented, all of which real POD vendors reject on:

- **Total ink coverage** (KDP/IngramSpark cap ~240–300% TAC). Needs per-pixel CMYK sampling.
- **Rich black / registration black** in body text — a 4-colour black at 9pt is a reprint.
- **Transparency and live blend modes** surviving into PDF/X-1a.
- **Annotations, form fields, embedded JS** — must be absent in a print PDF.
- **Spine width vs. actual page count and paper stock.** `geometry.py:69` computes spine
  from a `paper_basis` default; no profile supplies a real per-vendor stock caliper.
- **Gutter/creep** for the bound edge at high page counts.

Related profile gap: **no profile file declares a `composition:` block**, so the gate added
in `65f5834` runs entirely on its `maxOrphanPages=0` default. Verified against
`profiles/kdp/us-trade.yaml` — it carries trim, bleed, pdfSpec, coverSpec, proofSpec,
min/max pages, page multiple, and nothing else. There is also no `maxInkCoverage` for 2.2
to gate against when it is written.

**Work:** ink coverage and rich black are the two that actually cause rejections; both
need a rasterizer, which Ghostscript already provides. The annotations/JS check is nearly
free — `probe_pdf` (`:612`) already opens the file.

### 2.3 Eight override ops are declared but unimplementable

`services/structure/publisher_structure/overrides.py:357` — `_TRANSFORMS` implements four
ops (reclassify, retitle, delete, flag_ambiguity). `:368` — `UNIMPLEMENTED_OPS` names the
other eight the schema accepts: `split`, `merge`, `promote`, `demote`, `insert`, `rename`,
`set_attr`, `resolve_ambiguity`.

Since `dc6a6f3` these fail the build loudly instead of vanishing, and a test asserts the
two sets partition the schema's enum. So this is *safe*, not silent — but a user editing a
book still cannot split a run-on chapter, which is the single most common real fix.

**Work:** `split` and `merge` are the two worth writing. `set_attr` is nearly free and
subsumes `rename`/`retitle`.

---

## Tier 3 — never built

### 3.1 Zero real DOCX files

`find . -name '*.docx'` returns **0**. `corpus/manuscripts/` holds six synthetic
`.ast.json` files generated by `corpus/generator/generate.py`. The ingest path
(`services/ingest/publisher_ingest/docx_to_ast.py`) — the single component most exposed to
real-world mess (tracked changes, nested tables, EMF images, field codes, styles that lie)
— has never been run against a document Word produced.

`corpus/raster-diff/compare.py` exists; nothing in CI invokes it.

**Work:** highest ratio of bugs-found to effort in this entire file. Three real manuscripts
would probably break ingest in ways no synthetic fixture can.

### 3.2 CVE gate is structurally incapable of failing

`platform/supply-chain/publisher_supply_chain/__init__.py:88` — `CveGate.scan` iterates
`sbom._dependencies` and looks each up in `self._known_cves`, a dict initialised empty in
`__init__` and never written to. The findings list is therefore always empty and `passed`
is always `True`.

This is **honestly disclosed** at `ci.yml:160-162` ("CveGate is a placeholder"), and the
real gates in that job are `pip-audit --require-hashes` and `npm audit --audit-level=high`,
both of which do fail. So the risk is documentation, not security.

**Work:** either populate it from Trivy/Grype as the docstring says, or delete it and let
pip-audit/npm audit be the whole story. Deleting is smaller.

### 3.3 `packages/web` is unbuilt and untested in CI

`ci.yml:186-190` runs `npm ci` and `npm audit` for `packages/web` — and nothing else. No
`next build`, no `tsc --noEmit`, no tests. Compare `packages/api`, which gets a type-check
(`:104`) and a Vitest suite (`:106`) over three test files.

`packages/web` has zero test files. Given 1.1 (nothing mounts the panel), a type error in
`StructureReviewPanel.tsx` would reach `master` unnoticed.

**Work:** add `tsc --noEmit` to the web CI job. One line, and it is the check that would
have caught the missing route.

### 3.4 No scheduled CI

`.github/workflows/ci.yml:11-17` triggers on `push` and `pull_request` only. There is no
`schedule:` and no `workflow_dispatch:`. `platform/reproducibility/` implements a full
`ReproducibilityChecker` (manifest compare, toolchain compare, byte compare) that nothing
runs periodically — and byte-reproducibility is precisely the property that decays from
*outside* changes (a base image bump, a font update), which push-triggered CI cannot see.

**Work:** a nightly `schedule:` running the reproducibility checker. Small.

### 3.5 Inference is wired but incomplete

`services/structure/publisher_structure/inference.py:257` — `OpenRouterProvider` is a real
network client (auth, retry on 429/5xx, response parsing). Its own docstring (`:268-273`)
names what it deliberately does not do:

- no `response_format` structured-output enforcement — the model can return prose and the
  parse will fail at runtime rather than being prevented;
- no reasoning-effort or provider-pinning, though `platform/routing/policy.yaml` has a
  schema for both;
- `PromptCacheManager` still returns a placeholder prefix, so prompt caching saves nothing.

**Work:** `response_format` first — it is the one that converts a class of runtime failures
into impossible states.

---

## Not missing (so this stops being re-investigated)

- **Temporal.** `worker_temporal.py` and `platform/orchestration/py/` exist and work; the
  path is opt-in behind `docker compose --profile temporal`. Its tests are marked
  `requires_temporal` and deselected by default, by design.
- **epub / onix / manuscript-advisory / idml.** All four are real registered stages
  (`stages/secondary_output_stages.py:46`, `:85`, `stages/advisory_stage.py:35`,
  `stages/idml_stage.py:44`). `idml/1` being terminal is deliberate, documented, and
  correct — InDesign composes at open time.
- **Cover pipeline.** Four stages plus geometry and preflight, with a real OpenRouter image
  adapter (`services/cover/publisher_cover/image_gen_port.py:81`).
- **`finish-gs` failing locally.** Ghostscript 10.07.1 toolchain mismatch, documented in
  `a31c9bb`. Not a code defect.
- **`allow_stub_engines`.** Dev-only escape hatch, defaults `False`, worker never sets it.
  Working as designed.

---

## If you do three things

1. **§3.1 — get real DOCX files into `corpus/`.** Cheapest, and the only item here likely
   to surface bugs nobody has predicted.
2. **§1.3 — carry `confidence` from `rules.py` into the AST.** One stage edit. It is the
   single blocker behind the hardcoded `confidence: 1.0` and `lowConfidenceNodes: []` in
   the API, and therefore behind any honest review UI.
3. **§1.1 — persist override ops on PATCH and feed them to a build's root input.** Closes
   the one loop where every other piece is already built and tested.
