---
name: publisher-stages
description: Map of the `stages/` folder — the build-graph stage definitions (acquire, ingest, extract, ast-assemble, resolve, design-compile, paginate, finish, finish-gs, preflight, package, cover, cover-brief/art/judge/compose, cover-preflight, idml, epub, onix, structure-infer, manuscript-advisory). Use this whenever a task touches the pipeline DAG, adds or edits a stage, changes what a stage consumes or produces, bumps a @stage version, or asks "which stage does X" / "why is this stage unreachable". Read before editing any file under stages/.
---

# `stages/` — the build-graph stage definitions

Every book and cover build step lives here as a `@stage(...)`-decorated function. This
folder is *declarations*; the machinery that reads them lives in `platform/stages/`
(see the **publisher-platform** skill).

## The one idea

**The DAG is derived, never hand-wired.** The executor builds the graph by matching one
stage's output schema ID to another's input schema ID. Adding an edge means changing a
declaration in this folder — never wiring in `tracer_bullet.py` or `worker.py`.

Corollary that catches people: **when a chain is unreachable, fix the chain.** Do not
promote an input to `root_inputs`, and do not set `allow_stub_engines`. Both route around
a gate. `stages/paginate_stage.py` carries a long comment on exactly this, because
promoting `doc_path` to root would let a build reach a PDF without the text-integrity gate.

## Files

| File | What it declares |
|---|---|
| `__init__.py` | Imports every stage module — **importing this package is what registers the stages**. It was once empty, which silently emptied the registry and made the generated contract tests produce zero cases. **This is the only list**: `integrity.py`, `tracer_bullet.py` and `worker.py` all just `import stages`. (Its own docstring still says to keep a second list in sync with `integrity.py` — that is stale; the duplicate was deleted after it drifted.) |
| `acquire_stage.py` | `acquire` · `fixture-manifest/1 → raw-source/1`. Copies a fixture manuscript into CAS. `implements="ingest"` — the fixture alternative, used by the local dev harness. |
| `ingest_stage.py` | `ingest` · `raw-docx/1 → raw-source/1`. Production front door: uploaded DOCX bytes → AST, via `publisher_ingest.docx_to_ast`. Also `implements="ingest"`. |
| `extract_stage.py` | `extract` · `raw-source/1 → typescript-html/1`. AST → flat "typescript" HTML. Owns `_ast_to_html`, which `paginate` reuses on purpose. |
| `structure_stage.py` | `ast-assemble` · `typescript-html/1 + raw-source/1 → ast/1` (+ terminal `integrity-report/1`). **Text-integrity gate.** Takes both sides of the comparison as required non-root inputs, so no path exists that runs with only one. |
| `text_stream.py` | Not a stage. `ast_text(ast)` — the AST's side of every integrity comparison (titles, captions, epigraph sources included; spaces only at block boundaries) — shared by `ast-assemble` and `epub` so the two cannot drift. |
| `resolve_stage.py` | `resolve` · `ast/1 + overrides/1 → doc-effective/1`. Applies the override log. `overrides_path` is an *optional* root input; absence means zero overrides, still routed through `apply_overrides`. |
| `design_compile_stage.py` | `design-compile` · `designspec/1 + profile/1 → text/css`. Emits CSS `@page` rules. Emits `bleed` as a CSS property so the renderer owns the box arithmetic; also enforces font licensing via `publisher_prepress.fontvault`. |
| `paginate_stage.py` | `paginate` · `doc-effective/1 + text/css → raw-pdf/1` (+ terminal `pagemap/1`). Neither input is root — that is the DAG bypass fix. |
| `finish_stage.py` | `finish` · `raw-pdf/1 + profile/1 → pdfx/1` (+ terminal `proof-pdf/1`, `finish-report/1`). Real Ghostscript CMYK/PDF-X-1a conversion via `publisher_prepress.ghostscript`. Stub path reports `"status": "stub"`, never `"passed"`. |
| `structure_infer_stage.py` | `structure-infer` (E6.2) · `typescript-html/1 (+api_key root) → classification/1` (terminal). Real `InferenceGateway`/`OpenRouterProvider` call, guarded-reachable: `api_key` is a required root input with no producer, supplied by `worker.py` only when `OPENROUTER_API_KEY` is configured — the same reachability mechanism that keeps the cover pipeline absent from a plain interior build, not an env-var check inside the stage. Runs in parallel with `ast-assemble` (both consume `extract`'s output); never feeds `resolve`/`paginate` (D9: no LLM output enters a deterministic stage). Needs `infra/llm-egress/` (E3.1's egress allowlist proxy) for the worker to reach OpenRouter at all in production. |
| `prepress_stages.py` | **Four stages in one module** — grep for the name, not a filename: `preflight` (`pdfx/1 + profile/1 → preflight/1`, the delivery gate), `cover` (`page-count/1 + profile/1 → cover-geometry/1`), `cover-preflight` (`cover-raw-pdf/1 + profile/1 → cover-preflight/1`), and `finish-gs` (`implements="finish"`, same I/O as `finish`). |
| `package_stage.py` | `package` · `preflight/1 → build-report/1`. Terminal. Declaring `preflight_report` as a **required non-root** input is what makes packaging without a preflight verdict structurally impossible. |
| `cover_stages.py` | The cover art pipeline: `cover-brief` (`title-meta/1 + designspec/1 → art-brief/1`), `cover-art` (`art-brief/1 → cover-art/1 + art-provenance/1`), `cover-judge` (`cover-art/1 → art-ranking/1`), `cover-compose` (`cover-art/1 + cover-geometry/1 + designspec/1 + title-meta/1 → cover-raw-pdf/1`). |
| `idml_stage.py` | `idml` · `doc-effective/1 (+pagemap/1, designspec/1, profile/1) → idml/1` (terminal). InDesign-openable deliverable via `publisher_idml`/pandoc html→icml. **Opt-in only**: registered via `import_idml_if_requested(emit_idml)`, called from each entry point when `PUBLISHER_EMIT_IDML` is set — needs pandoc, and both its root inputs are optional, so once registered it is unconditionally reachable on every build. Import is the only gate. |
| `secondary_output_stages.py` | (E7.2) **Two stages**: `epub` (`doc-effective/1 → epub/1`) and `onix` (`doc-effective/1 → onix/1`), both terminal. `epub` renders with `rendering.ast_to_epub_sections` (the print gate's renderer, with linked footnotes), pulls figures out of CAS (TIFF/BMP → PNG), packages with `publisher_epub.EPUB3Writer`, then **fails unless the stored package's text (`spine_text`) equals the document's (`text_stream.ast_text`)**. `onix` wraps `publisher_onix.ONIXWriter`. Neither needs an external toolchain, so — unlike `idml` — both register **unconditionally**: every ordinary build now produces an EPUB and ONIX metadata file alongside the interior PDF, which is what makes ARCHITECTURE.md's documented delivery step (§1.1 step 9) true. |
| `advisory_stage.py` | (E7.2) `manuscript-advisory` · `raw-docx/1 (root) → advisory-report/1` (terminal). Wraps `publisher_alttext.doctor.ManuscriptDoctor` — format/size/structural warnings on the uploaded bytes, pre-ingest. Runs in **parallel** with `ingest` off the same uploaded bytes under its own root input (`worker.py`'s `_initial_inputs_for` supplies it unconditionally); never feeds `ingest` or anything downstream — advisory output cannot change what a build produces. Sniffs the CAS blob's real format by magic bytes (reuses `ingest_stage._is_legacy_doc`) since a content-addressed path has no file extension for `ManuscriptDoctor` to read. |
| `tests/test_ast_assemble.py` | Mutation test for the integrity gate: delete a paragraph, the build must fail. |
| `tests/test_bleed_geometry.py` | Asserts on `design_compile_stage._emit_css()` output only — it imports no other stage. The chain it reasons about is `design-compile` (grows the page box) → `finish` (insets TrimBox) → `preflight` (measures); only the first is executed here. |
| `tests/test_ingest_security.py` | U5/S9 DOCX ingest hardening — zip bombs, entry caps, traversal. |
| `tests/test_secondary_outputs.py` | (E7.2) `epub`/`onix` stage behaviour (rich content, the lossy-EPUB mutation, TIFF conversion, href encoding, and EPUBCheck when `PUBLISHER_EPUBCHECK_JAR` is set) + a `plan()` reachability test proving both are in an ordinary build's reachable set (always-on, not gated like `idml`). |
| `tests/test_advisory.py` | (E7.2) `manuscript-advisory` stage behaviour, the magic-byte format sniff, and `plan()` reachability tests proving it is gated on its OWN root input, separate from `ingest`'s. |

> **Versions are deliberately not listed here.** `tools/lint_stage_versions.py` forces
> `@stage(version=N)` to change on every touched stage module, so any copy in prose rots on
> the next stage PR and nothing checks it. Read the current version from the declaration, or
> from `get_registry()`. The schema IDs below *are* durable — they are the DAG's edges.

## The derived graph

```
                    ┌ acquire (fixture) ┐
                    └ ingest  (real)    ┘ → raw-source/1
raw-source/1 → extract → typescript-html/1 ┐
raw-source/1 ──────────────────────────────┴→ ast-assemble → ast/1  [+integrity-report]
typescript-html/1 (+api_key root) → structure-infer → classification/1  (terminal, parallel,
                                                        reachable only with OPENROUTER_API_KEY)
raw-docx/1 (root) → manuscript-advisory → advisory-report/1     (terminal, parallel, never feeds ingest)
ast/1 (+overrides/1) → resolve → doc-effective/1 ┐
designspec/1 + profile/1 → design-compile → text/css ┘
                                        → paginate → raw-pdf/1  [+pagemap]
raw-pdf/1 + profile/1 → finish | finish-gs → pdfx/1  [+proof-pdf, finish-report]
pdfx/1 + profile/1 → preflight → preflight/1
preflight/1 → package → build-report/1                          (terminal)

doc-effective/1 → epub → epub/1                                 (terminal, always-on)
doc-effective/1 → onix → onix/1                                 (terminal, always-on)
doc-effective/1 (+pagemap/1, designspec/1, profile/1) → idml → idml/1   (terminal, opt-in: PUBLISHER_EMIT_IDML)

cover (parallel, page-count-free until compose):
title-meta/1 + designspec/1 → cover-brief → art-brief/1 → cover-art → cover-art/1
cover-art/1 → cover-judge → art-ranking/1                       (terminal, human gate 3)
page-count/1 + profile/1 → cover → cover-geometry/1
cover-art/1 + cover-geometry/1 + designspec/1 + title-meta/1 → cover-compose → cover-raw-pdf/1
cover-raw-pdf/1 + profile/1 → cover-preflight → cover-preflight/1
```

`cover-art` runs in parallel with the whole interior build. Only `cover-compose` waits on
the interior's final page count — that split keeps slow, nondeterministic image generation
off the critical path.

## Rules that bite

- **Bump `version=N` on any behavioural change.** `tools/lint_stage_versions.py` diffs
  against the merge base and fails CI if a touched stage module's version is unchanged.
  A module-level bump (all stages in one file) is the convention when the shared code moves.
- **Write artifacts through `ctx.cas_root`, never `ctx.work_dir`** — work_dir is scratch
  and is deleted.
- **`terminal_outputs=[...]`** marks an output that is delivered via the API and never
  consumed by another stage. Without it, DAG integrity reports it as an orphan.
- **Two stages may share `implements="..."`** to be alternative implementations of one
  logical step (`acquire`/`ingest`, `finish`/`finish-gs`). Exactly one is selected per
  process.
- Two PDF lineages exist on purpose: interior `raw-pdf/1` vs cover `cover-raw-pdf/1`.
  Sharing the kind gave `raw-pdf/1` two producers and bound consumers to whichever ran first.

## Adding a stage — checklist

1. Write the module here; decorate with `@stage(name, version, inputs, outputs, ...)`.
2. Add the import to `stages/__init__.py` — **that is the only place**. `platform/stages/integrity.py` does `import stages` and has no list of its own; adding one re-creates the hand-maintained duplicate that already drifted (it once omitted `prepress_stages` entirely).
3. If the input/output schema is new, add it under `schemas/` (**publisher-schemas**).
4. Point `fixtures="fixtures/<stage>/v1"` at a fixture set, or `None` (**publisher-fixtures**).
5. Run `python platform/stages/integrity.py` — needs the editable installs first (`pip install -e platform/... -e services/...`, see `.github/workflows/ci.yml`). Non-zero means a DAG violation **or** a missing install — read the output rather than assuming.
6. Run `./scripts/test.ps1` (or `scripts/test.sh`), never bare `pytest`.

## Related

`platform/stages/` (registry + integrity checker) · `services/` (the real work each stage
calls into) · `tests/contracts/` (generated per-stage contract tests) ·
`docs/ARCHITECTURE.md` §1.2 (canonical stage list) · `docs/BUILD_PLAN.md` §3 ·
`infra/llm-egress/` + `docker-compose.yml`'s `llm-egress`/`llm-egress-uplink` networks
(E3.1/E6.2 -- the allowlist proxy `structure-infer` needs; the worker has no other
route to the internet) · `services/structure/publisher_structure/inference.py`
(`InferenceGateway`, `OpenRouterProvider`) · `docs/ARCHITECTURE_SCORE_10_PLAN.md` E6.
