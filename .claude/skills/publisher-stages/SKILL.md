---
name: publisher-stages
description: Map of the `stages/` folder — the build-graph stage definitions (acquire, ingest, extract, ast-assemble, resolve, design-compile, paginate, finish, finish-gs, preflight, package, cover, cover-brief/art/judge/compose, cover-preflight). Use this whenever a task touches the pipeline DAG, adds or edits a stage, changes what a stage consumes or produces, bumps a @stage version, or asks "which stage does X" / "why is this stage unreachable". Read before editing any file under stages/.
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
| `__init__.py` | Imports every stage module — **importing this package is what registers the stages**. It was once empty, which silently emptied the registry and made the generated contract tests produce zero cases. Keep the import list in sync with `platform/stages/integrity.py`. |
| `acquire_stage.py` | `acquire` v3 · `fixture-manifest/1 → raw-source/1`. Copies a fixture manuscript into CAS. `implements="ingest"` — the fixture alternative, used by the local dev harness. |
| `ingest_stage.py` | `ingest` v1 · `raw-docx/1 → raw-source/1`. Production front door: uploaded DOCX bytes → AST, via `publisher_ingest.docx_to_ast`. Also `implements="ingest"`. |
| `extract_stage.py` | `extract` v1 · `raw-source/1 → typescript-html/1`. AST → flat "typescript" HTML. Owns `_ast_to_html`, which `paginate` reuses on purpose. |
| `structure_stage.py` | `ast-assemble` v3 · `typescript-html/1 + raw-source/1 → ast/1` (+ terminal `integrity-report/1`). **Text-integrity gate.** Takes both sides of the comparison as required non-root inputs, so no path exists that runs with only one. |
| `resolve_stage.py` | `resolve` v1 · `ast/1 + overrides/1 → doc-effective/1`. Applies the override log. `overrides_path` is an *optional* root input; absence means zero overrides, still routed through `apply_overrides`. |
| `design_compile_stage.py` | `design-compile` v3 · `designspec/1 + profile/1 → text/css`. Emits CSS `@page` rules. Emits `bleed` as a CSS property so the renderer owns the box arithmetic; also enforces font licensing via `publisher_prepress.fontvault`. |
| `paginate_stage.py` | `paginate` v4 · `doc-effective/1 + text/css → raw-pdf/1` (+ terminal `pagemap/1`). Neither input is root — that is the DAG bypass fix. |
| `finish_stage.py` | `finish` v7 · `raw-pdf/1 + profile/1 → pdfx/1` (+ terminal `proof-pdf/1`, `finish-report/1`). Real Ghostscript CMYK/PDF-X-1a conversion via `publisher_prepress.ghostscript`. Stub path reports `"status": "stub"`, never `"passed"`. |
| `prepress_stages.py` | Four stages in one module: `preflight` v6 (`pdfx/1 → preflight/1`, the delivery gate), `cover` v3 (`page-count/1 + profile/1 → cover-geometry/1`), `cover-preflight` v2 (`cover-raw-pdf/1 → cover-preflight/1`), and `finish-gs` v8 (`implements="finish"`, same I/O as `finish`). |
| `package_stage.py` | `package` v3 · `preflight/1 → build-report/1`. Terminal. Declaring `preflight_report` as a **required non-root** input is what makes packaging without a preflight verdict structurally impossible. |
| `cover_stages.py` | The cover art pipeline: `cover-brief` v1 (`title-meta/1 + designspec/1 → art-brief/1`), `cover-art` v1 (`art-brief/1 → cover-art/1 + art-provenance/1`), `cover-judge` v1 (`cover-art/1 → art-ranking/1`), `cover-compose` v1 (`cover-art/1 + cover-geometry/1 + designspec/1 + title-meta/1 → cover-raw-pdf/1`). |
| `tests/test_ast_assemble.py` | Mutation test for the integrity gate: delete a paragraph, the build must fail. |
| `tests/test_bleed_geometry.py` | Bleed must survive `design-compile` → `paginate` → `finish` agreeing on one number. |
| `tests/test_ingest_security.py` | U5/S9 DOCX ingest hardening — zip bombs, entry caps, traversal. |

## The derived graph

```
                    ┌ acquire (fixture) ┐
                    └ ingest  (real)    ┘ → raw-source/1
raw-source/1 → extract → typescript-html/1 ┐
raw-source/1 ──────────────────────────────┴→ ast-assemble → ast/1  [+integrity-report]
ast/1 (+overrides/1) → resolve → doc-effective/1 ┐
designspec/1 + profile/1 → design-compile → text/css ┘
                                        → paginate → raw-pdf/1  [+pagemap]
raw-pdf/1 + profile/1 → finish | finish-gs → pdfx/1  [+proof-pdf, finish-report]
pdfx/1 + profile/1 → preflight → preflight/1
preflight/1 → package → build-report/1                          (terminal)

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
2. Add the import to `stages/__init__.py` **and** to `platform/stages/integrity.py`.
3. If the input/output schema is new, add it under `schemas/` (**publisher-schemas**).
4. Point `fixtures="fixtures/<stage>/v1"` at a fixture set, or `None` (**publisher-fixtures**).
5. Run `python platform/stages/integrity.py` — non-zero means a real DAG violation.
6. Run `./scripts/test.ps1` (or `scripts/test.sh`), never bare `pytest`.

## Related

`platform/stages/` (registry + integrity checker) · `services/` (the real work each stage
calls into) · `tests/contracts/` (generated per-stage contract tests) ·
`docs/ARCHITECTURE.md` §1.2 (canonical stage list) · `docs/BUILD_PLAN.md` §3.
