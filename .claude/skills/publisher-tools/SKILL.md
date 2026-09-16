---
name: publisher-tools
description: "Map of the `tools/` folder — the CI-blocking Python lints: schema free-text, stage version-bump, service dependency-declaration, tenant scoping, and docs claims. Import boundaries are a separate mechanism (`.importlinter` + `lint-imports`, not a tools/*.py script). Use this whenever a lint fails in CI, a task adds a schema field or a stage, a package gains a cross-package import, a route touches app.tenant_id, a doc cites a path/count/identifier, or you need to know which gates must pass before pushing. Read before editing anything under tools/."
---

# `tools/` — the CI gates

Five standalone scripts, plus one declarative mechanism that lives outside this folder
(`.importlinter`). All are **CI-blocking**, all registered in `tests/meta/
test_gates_can_fail.py`'s `GATES` (E0.1) with a mutation proving each can actually fail —
several used to carry `continue-on-error: true` or otherwise couldn't fail, which made the
doctrine gate advisory while the pipeline stayed green regardless of what it found.

| File | What it enforces | Run it |
|---|---|---|
| `lint_schemas.py` | **F3.1 (finding 10): no free-text string fields in structure-route schemas.** Only an explicit allowlist of provenance fields is permitted. This is what keeps LLM output closed-enum; `services/alttext` is the single approved exception, and it lives in its own service precisely so the invariant stays absolute everywhere else. | `python tools/lint_schemas.py` |
| `lint_stage_versions.py` | **BUILD_PLAN.md §3.3: touched stage module ⇒ `@stage(version=N)` must change.** Diffs against the merge base and flags touched stage modules whose version is unchanged. It once swallowed all its own errors, so it could not fail. | `python tools/lint_stage_versions.py <git-ref>` — **positional; there is no `--base` flag.** Passing `--base` makes it treat that literal string as the ref, `git diff` fails, and the gate prints "no stage files changed" and exits 0 having checked nothing. |
| `lint_service_deps.py` | **U3: every `services/*` package declares in its own `pyproject.toml` any `publisher_*` package it imports.** AST-parses the shipped package (not its tests). Declarations were `dependencies = []` across the board and cross-package imports resolved only because `Dockerfile.worker` puts everything on one PYTHONPATH — so a packaging change broke *production* silently instead of CI loudly. | `python tools/lint_service_deps.py` |
| `lint_tenant_scoping.py` | **E4.3: `app.tenant_id` (the RLS session GUC) is set in exactly one place** — `withTenant` in `packages/api/src/db.ts`. Greps every other TS file in `packages/api/src` for `set_config('app.tenant_id'` or a literal `SET ... app.tenant_id` and fails if either appears outside `db.ts`. RLS is only real if the GUC can't be set to a value that never passed through auth. | `python tools/lint_tenant_scoping.py` |
| `lint_docs_claims.py` | **E7.3, closes `L20`**: five mechanically-checkable doc claims — every path in CLAUDE.md's folder-map table exists; ARCHITECTURE.md's §2.14 repo tree matches the filesystem (both directions: cited paths exist, paths the text says don't exist really don't); CLAUDE.md's stage count matches the registry; the stage names CLAUDE.md's `stages/` row cites resolve in the registry; every work-item identifier (`D8`, `U5`, `A3`, `E7.2`, …) referenced anywhere under `docs/`/`CLAUDE.md` is defined somewhere (a heading, a **bold** anchor, or a table's own ID column). Deliberately narrow per-check, not a general prose linter — a gate with false positives gets disabled (E0.1's own doctrine). | `python tools/lint_docs_claims.py` |

Import boundaries (**E1.4**) are enforced separately: `.importlinter` (repo root) declares
`no-platform-to-services`, `services-do-not-know-the-substrate`, `services-are-independent`,
and `no-production-import-of-the-dev-harness` as **declarative contracts** for the
`lint-imports` CLI (`grimp`-backed), not a `tools/*.py` script — `lint_service_deps.py`
still does the one thing import-linter cannot (checking a cross-package import is also
*declared* in the importing package's own `pyproject.toml`). Run: `lint-imports --config
.importlinter`.

## The full gate set before pushing

```bash
python tools/lint_schemas.py
python tools/lint_service_deps.py
python tools/lint_stage_versions.py HEAD~1   # positional ref -- never --base
python tools/lint_tenant_scoping.py
python tools/lint_docs_claims.py
lint-imports --config .importlinter     # import boundaries (E1.4) -- needs the editable
                                        # installs on PYTHONPATH, see PUBLISHER_PKGS in ci.yml
python platform/stages/integrity.py     # DAG integrity — run as a SCRIPT, not -m
cd schemas && node codegen/test.mjs     # generated types actually execute
cd schemas && node codegen/test.mjs     # codegen: the ONLY real check here.
                                        # `gen:check` / `git diff -- '**/*.gen.*'` always
                                        # exits 0 -- .gen.* are gitignored and untracked.
bash scripts/test.sh                    # never bare pytest (no exec bit: use `bash`)
cargo test 2>&1 | tail -30 && cargo clippy -- -D warnings 2>&1 | tail -30
```

`platform/stages/integrity.py` is not in `tools/` but belongs to this set — see
**publisher-platform** for why it must be invoked as a script.

## Rules that bite

- **A lint that swallows its own errors is worse than no lint.** Several of these were in
  that state. If you touch one, make sure it can still fail — `tests/meta/
  test_gates_can_fail.py` has a mutation-based test proving each one still can; add a
  `Gate` entry there for any new lint, in the same PR.
- **These are gates, not suggestions.** Routing around one (promoting a root input,
  allowlisting a free-text field, skipping a version bump, setting `app.tenant_id` outside
  `withTenant`) is the failure mode they exist to catch.
- **`lint_docs_claims.py` is narrow on purpose.** Extend it by adding a new bounded check,
  not by making an existing one scan more broadly — a generic prose-wide path/identifier
  scanner produces false positives on code snippets, schema IDs (`ast/1`), and unrelated
  identifier collisions (e.g. `T1` means a threat-model row in `ARCHITECTURE_SCORE_10_PLAN.md`
  and a parallelization track in `PARALLELIZATION.md` — two different schemes, same shape).

## Related

`.github/workflows/ci.yml` (where they run) · `tests/meta/test_gates_can_fail.py` (E0.1 —
proves each one can fail) · `.importlinter` · `schemas/` · `stages/` · `services/` ·
`docs/BUILD_PLAN.md` §3.3, §7 · `docs/ARCHITECTURE_UPLIFT_PLAN.md` U3 ·
`docs/ARCHITECTURE.md` §2.15 · `docs/ARCHITECTURE_SCORE_10_PLAN.md` E1.4, E4.3, E7.3.
