# Publisher

**DOCX → print-ready book PDF** (and EPUB, IDML, ONIX). A content-addressed build system for book production.

## Status

Foundation phase — schemas, CAS, stage registry, and codegen pipeline.

| Module | Status |
|---|---|
| `schemas/` (JSON Schema) | ✅ v0.1 — AST, OverrideSet, DesignSpec, Profile, Preflight, Manifest, Classification, AgentProposal, Pagemap |
| `schemas/ts/` (Zod) | ✅ Generated TypeScript types |
| `schemas/py/` (Pydantic) | ✅ Generated Python models |
| `platform/cas` | ✅ Hash types + streaming put/get + atomic writes (Python + Rust) |
| `platform/stages` | ✅ Stage registry, DAG derivation, topological sort |
| `platform/cache` | ✅ Cache key computation, toolchain digest |
| `platform/sandbox` | ✅ Baseline sandbox (process isolation) |
| `fixtures/` | ✅ Fixture manifests (v1 for AST, design-compile, preflight) |
| `corpus/` | ✅ Synthetic generator (O8), golden corpus skeleton |

## Quick start

```bash
# Python tests
pip install -e platform/cas/py -e platform/stages/py -e platform/cache/py
python -m pytest platform/cas/py/tests platform/stages/py/tests -v

# Rust tests
cargo test

# Generate synthetic corpus
python corpus/generator/generate.py

# Regenerate types from schemas
cd schemas && node codegen/generate.mjs
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

### Key principles

1. **Content-addressed build graph** — every stage is `f(inputs, params, toolchain) → artifacts`, keyed by hash
2. **AST derived and immutable** — human *and agent* decisions are separate override ops
3. **AST schema == ProseMirror schema == input to every writer** — one schema, no drift
4. **Schema-first codegen** — JSON Schema → Pydantic + Zod, CI-verified
5. **Stages are declared, not wired** — DAG, tests, and cache keys derived from declarations

### Stack

| Tier | Language | Where |
|---|---|---|
| Orchestration / IO-bound | TypeScript (Node 22+) | API, orchestrator, web, render workers, agents |
| Document processing | Python 3.12+ | Ingest, structure, design, prepress, EPUB, IDML, inference |
| Hot paths | Rust | CAS hash, docxstream, pagescan, pdfprobe |

## Project structure

```
schemas/          # JSON Schema source of truth
platform/         # CAS, cache, stages, sandbox, telemetry, fontvault
services/         # Ingest, structure, design, prepress, EPUB, IDML
packages/         # API, web, orchestrator, worker-render
fixtures/         # Stage fixture sets (versioned)
corpus/           # Golden manuscripts + raster diff harness
profiles/         # Vendor specs (KDP, IngramSpark, Lulu)
templates/        # DesignSpec presets
```

## License

Proprietary — all rights reserved.
