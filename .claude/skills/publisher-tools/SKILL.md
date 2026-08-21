---
name: publisher-tools
description: Map of the `tools/` folder — the three CI-blocking lints: schema free-text lint, stage version-bump lint, and service dependency-declaration lint. Use this whenever a lint fails in CI, a task adds a schema field or a stage, a package gains a cross-package import, or you need to know which gates must pass before pushing. Read before editing anything under tools/.
---

# `tools/` — the CI gates

Three standalone scripts. All three are **CI-blocking** — they used to carry
`continue-on-error: true`, which made every doctrine gate advisory and let the pipeline stay
green regardless of what they found.

| File | What it enforces | Run it |
|---|---|---|
| `lint_schemas.py` | **F3.1 (finding 10): no free-text string fields in structure-route schemas.** Only an explicit allowlist of provenance fields is permitted. This is what keeps LLM output closed-enum; `services/alttext` is the single approved exception, and it lives in its own service precisely so the invariant stays absolute everywhere else. | `python tools/lint_schemas.py` |
| `lint_stage_versions.py` | **BUILD_PLAN.md §3.3: touched stage module ⇒ `@stage(version=N)` must change.** Diffs against the merge base and flags touched stage modules whose version is unchanged. It once swallowed all its own errors, so it could not fail. | `python tools/lint_stage_versions.py [--base <git-ref>]` |
| `lint_service_deps.py` | **U3: every `services/*` package declares in its own `pyproject.toml` any `publisher_*` package it imports.** AST-parses the shipped package (not its tests). Declarations were `dependencies = []` across the board and cross-package imports resolved only because `Dockerfile.worker` puts everything on one PYTHONPATH — so a packaging change broke *production* silently instead of CI loudly. | `python tools/lint_service_deps.py` |

## The full gate set before pushing

```bash
python tools/lint_schemas.py
python tools/lint_service_deps.py
python tools/lint_stage_versions.py HEAD~1
python platform/stages/integrity.py     # DAG integrity — run as a SCRIPT, not -m
cd schemas && node codegen/test.mjs     # generated types actually execute
git diff --exit-code -- '**/*.gen.*'    # codegen in sync
./scripts/test.ps1                      # or scripts/test.sh — never bare pytest
cargo test && cargo clippy -- -D warnings
```

`platform/stages/integrity.py` is not in `tools/` but belongs to this set — see
**publisher-platform** for why it must be invoked as a script.

## Rules that bite

- **A lint that swallows its own errors is worse than no lint.** Two of these three were in
  that state. If you touch one, make sure it can still fail.
- **These are gates, not suggestions.** Routing around one (promoting a root input,
  allowlisting a free-text field, skipping a version bump) is the failure mode they exist
  to catch.

## Related

`.github/workflows/ci.yml` (where they run) · `schemas/` · `stages/` · `services/` ·
`docs/BUILD_PLAN.md` §3.3, §7 · `docs/ARCHITECTURE_UPLIFT_PLAN.md` U3 ·
`docs/ARCHITECTURE.md` §2.15.
