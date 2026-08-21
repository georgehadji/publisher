---
name: publisher-fixtures
description: Map of the `fixtures/` folder — the versioned per-stage fixture sets that the generated contract tests run over (ast, cover, cover-brief, design-compile, extract, finish, finish-gs, manuscripts, paginate, preflight, structure). Use this whenever a task adds or changes a stage's `fixtures=` declaration, makes a contract test fail, or asks "where do the test inputs for stage X live". Read before editing anything under fixtures/.
---

# `fixtures/` — versioned stage fixture sets

Each subfolder is one stage's fixture set, versioned (`v1/`). A stage points at one with
`fixtures="fixtures/<name>/v1"` in its `@stage(...)` declaration; `tests/contracts/
test_generated.py` then enumerates the registry and turns every declared fixture set into a
parametrized contract case.

Every set is a `manifest.json` conforming to `fixture-manifest/1`, with the same shape:

```json
{
  "schema": "fixture-manifest/1",
  "fixtureSet": "ast/v1",
  "description": "...",
  "createdAt": "...",
  "generatedFrom": { "corpusManuscripts": [...], "toolchain": {...} },
  "inputs":  [ { "kind": "...", "description": "..." } ],
  "expectedOutputs": [ { "kind": "...", "schema": "ast/1", "description": "..." } ],
  "fixtures": [ { "name": "...", "inputFiles": {...}, ... } ]
}
```

`generatedFrom.toolchain` is what makes a fixture reproducible rather than a snapshot of
someone's laptop.

## The sets

| Path | Declared by | Contents |
|---|---|---|
| `manuscripts/v1/manifest.json` | `acquire` | Source manuscript fixtures — the inputs that enter the pipeline. 1 fixture. |
| `extract/v1/manifest.json` | `extract` | Tracer-bullet fixture for `extract/v1`. 1 fixture. |
| `ast/v1/manifest.json` | — | Canonical ASTs for contract testing (mini-novel, short-story). 2 fixtures. |
| `structure/v1/manifest.json` | — | Tracer-bullet fixture for `structure/v1`. 1 fixture. |
| `design-compile/v1/manifest.json` | `design-compile` | DesignSpec → CSS/IDML/Typst emitters. 2 fixtures. |
| `paginate/v1/manifest.json` | `paginate` | Tracer-bullet fixture for `paginate/v1`. 1 fixture. |
| `finish/v1/manifest.json` | `finish` | Tracer-bullet fixture for `finish/v1`. 1 fixture. |
| `finish-gs/v1/manifest.json` | `finish-gs` | Tracer-bullet fixture for `finish-gs/v1`. 1 fixture. |
| `preflight/v1/manifest.json` | `preflight` | Vendor profile gates. 2 fixtures. |
| `cover/v1/manifest.json` | `cover` | Tracer-bullet fixture for `cover/v1`. |
| `cover-brief/v1/manifest.json` | `cover-brief` | Title metadata + DesignSpec → ArtBrief (COVER_DESIGN.md §2). 1 fixture. |

Stages with `fixtures=None` (`cover-art`, `cover-judge`, `cover-compose`) are deliberately
excluded — they call external image models, so a fixture set would either be a network
call or a lie.

## Rules that bite

- **A manifest must agree with the stage's own declaration.** The contract test validates
  the fixture set against the registry: the outputs the stage promises, the inputs it
  consumes, and the schema IDs it names. A mismatch is a test failure, not a warning.
- **A missing expected file used to be a pass.** The old contract test called
  `pytest.skip()` when a declared output had no expected file, and ended with a literal
  `assert True`. Do not reintroduce either — the gate is supposed to be able to fail.
- **Bump the set version (`v1` → `v2`) rather than mutating a set in place** when the
  expected output legitimately changes; the stage's `fixtures=` path moves with it.

## Related

`stages/` (the `fixtures=` declarations) · `tests/contracts/test_generated.py` (the
consumer) · `corpus/` (the *golden* manuscripts these are generated from — a different
thing: fixtures are contract inputs, corpus is regression material) ·
`docs/BUILD_PLAN.md` §3.20.
