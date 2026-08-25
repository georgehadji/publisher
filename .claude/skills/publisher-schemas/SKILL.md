---
name: publisher-schemas
description: Map of the `schemas/` folder — the JSON Schema source of truth (ast, overrides, designspec, profile, preflight, manifest, classification, agent-proposal, pagemap, cover/*) and the codegen that turns them into Pydantic + Zod types. Use this whenever a task adds or changes an artifact schema, a stage's input/output contract, a schema ID, or the generated types; or asks "what shape is an AST / DesignSpec / PreflightReport". Read before editing anything under schemas/.
---

# `schemas/` — the source of truth

Every artifact that crosses a stage boundary has a JSON Schema here. Schema IDs are what
the DAG is derived from, so **a schema ID is an API**, not a label.

Two rules govern the whole folder:

1. **Schema-first codegen.** JSON Schema → Pydantic (`schemas/py/models.gen.py`) + Zod
   (`schemas/ts/index.gen.ts`). Never hand-edit them. **Neither exists in a fresh checkout**
   (`schemas/py/` is not even created until you run codegen) and neither is tracked — see the
   gen:check warning below before trusting any "in sync" claim.
2. **The AST schema is one schema, used three ways** — canonical book model, ProseMirror
   schema, and the input to every writer. That is what prevents drift.

## Files

| File | Schema ID | What it models |
|---|---|---|
| `ast/ast.schema.json` | `ast/1` | **Book AST** — the canonical semantic book model, derived from DOCX and consumed by all renderers. Derived and immutable. |
| `overrides/overrides.schema.json` | `overrides/1` | **OverrideSet** — human decisions layered on top of the AST, never mutating it. Addressed by `sourceRef`. |
| `designspec/designspec.schema.json` | `designspec/1` | **DesignSpec** — engine-agnostic typographic spec. One source, three emitters (CSS, IDML, Typst). |
| `profile/profile.schema.json` | `profile/1` | **OutputProfile** — vendor spec: trim, bleed, PDF version, colour space. Instances live in `profiles/`. |
| `preflight/preflight.schema.json` | `preflight/1` | **PreflightReport** — the delivery gate result. |
| `manifest/manifest.schema.json` | `manifest/1` | **BuildManifest** — immutable record of a completed build: every input, stage version, output artifact. |
| `classification/classification.schema.json` | `classification/1` | **ClassificationResult** — LLM output. **Closed-enum only, no prose, no free text.** |
| `agent-proposal/agent-proposal.schema.json` | `agent-proposal/1` | **AgentProposal** — an agent's proposed action, expressed as override ops, never AST writes. |
| `pagemap/pagemap.schema.json` | `pagemap/1` | **PageMap** — per-page layout metadata from `paginate`. Declared a **terminal output**: no stage consumes it. `platform/pagescan` (Rust) reads it but is not a stage; `stitch`/`folio`/`toc`/`index` appear in ARCHITECTURE/OPTIMIZATION plans and are **not implemented**. Do not remove `terminal_outputs=["pagemap"]` expecting a consumer. |
| `cover/art-brief.schema.json` | `art-brief/1` | **ArtBrief** — semantic cover direction. Closed enums everywhere except `concept`/`subject`. |
| `cover/art-provenance.schema.json` | `art-provenance/1` | **ArtProvenance** — compliance + reproducibility record for generated art. Ships in the delivery package; retailers require AI-generation disclosure. |
| `cover/cover-verdict.schema.json` | `cover-verdict/1` | **CoverVerdict** — one judge's pairwise vote. Closed enum, deliberately **no rationale field**. |
| `ts/shared.types.json` | `types/1` | **Nothing references it.** There is not one cross-file `$ref` in `schemas/`, and `codegen/generate.mjs` skips it (it only reads `*.schema.json`). Its `$defs` are duplicated inline in the individual schemas; codegen recovers shared types by content-hash dedup instead. Editing this file changes nothing. |
| `codegen/generate.mjs` | — | The two-pass generator. Two-pass because a single pass decided a type's *name* at point of use instead of registering types up front, which produced dangling refs and a literal `z.object({...PROPERTIES...})` placeholder. |
| `codegen/test.mjs` | — | **Executes** the generated types rather than grepping them. The previous version only ran substring checks and passed while the generated TS had 39 dangling refs and the generated Python was an unimportable SyntaxError. |
| `package.json` | — | `@publisher/schemas`. Scripts: `gen`, `gen:check`, `test`, `build`. Depends on `zod`. |

**The `.gitignore` comment above `*.gen.*` says "checked in" and is wrong.** Zero `.gen.*`
files are tracked, none exist in a fresh checkout, and `git add` refuses them. Consequently
`gen:check` (`git diff --exit-code -- '**/*.gen.*'`) **exits 0 unconditionally** — it inspects
only tracked files, so it can never detect drift. CI's step regenerates and then runs a bare
`git diff --exit-code`, which is equally blind to ignored files. Treat codegen freshness as
*unverified* and rerun `node codegen/test.mjs`, which actually executes the output.

## Working with schemas

```bash
cd schemas && node codegen/generate.mjs      # regenerate types
cd schemas && node codegen/test.mjs          # execute the generated types
python cli.py schema validate <file>         # validate an instance
python tools/lint_schemas.py                 # no free-text in structure-route schemas
# NOTE: gen:check cannot fail (see above) — codegen/test.mjs is the real check
```

## Rules that bite

- **No free-text string fields in structure-route schemas.** `tools/lint_schemas.py`
  enforces an explicit allowlist of provenance fields (F3.1, finding 10). The only
  approved free-text schema in the repo belongs to `services/alttext`.
- **Adding a schema means adding an edge.** If a stage declares it as an input/output, the
  DAG changes — re-run `python platform/stages/integrity.py`.
- **Bump the schema ID version** (`ast/1` → `ast/2`) for a breaking change; every stage
  declaring the old ID must be updated, and their `@stage(version=...)` bumped too.
- **Regenerate** after any schema edit and run `node codegen/test.mjs`. Do **not** try to
  commit the `.gen.*` files — they are gitignored, and no gate will catch stale ones for you.

## Related

`stages/` (declares these IDs) · `platform/stages/` (matches them into a DAG) ·
`profiles/` (instances of `profile/1`) · `templates/` (instances of `designspec/1`) ·
`fixtures/` (instances used by contract tests) · `tools/lint_schemas.py`.
