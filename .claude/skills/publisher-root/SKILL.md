---
name: publisher-root
description: Map of the Publisher repo root — the two execution tiers (`tracer_bullet.py` dev harness and `worker.py` production worker), `cli.py`, `conftest.py`, Docker/compose, the Python/Node/Rust manifests, `.github/workflows/ci.yml`, and `.reasonix/`. Use this whenever a task touches running a build, the worker queue or DAG executor, the CLI, containers, dependency pinning, CI configuration, or asks "how do I run this thing". Read before editing any file at the repo root.
---

# Repo root — how the system actually runs

## Two execution tiers, and the difference matters

| | `tracer_bullet.py` | `worker.py` |
|---|---|---|
| Role | Local dev harness | Production |
| Scope | One build, prints to stdout | Many builds, many workers |
| State | `SqliteCacheStore`, no DB | Postgres, state outlives the process |
| Engines | **Stubs allowed** | `allow_stub_engines` never set |
| Ingest | Selects `acquire` (synthetic corpus) | Uses `ingest` (the tenant's DOCX) |

**`allow_stub_engines` is a dev-only escape hatch, default `False`.** It exists so a missing
renderer fails loudly instead of silently certifying stub output as press-ready. If a chain
is unreachable, fix the chain — do not promote an input to root and do not set this flag.

## Files

| File | What it does |
|---|---|
| `tracer_bullet.py` | `DagExecutor` — the executor itself: resolves the reachable stage set, wires upstream outputs into downstream inputs by matching declared schema IDs, computes cache keys, runs stages. `run_tracer_bullet(manuscript, profile)` is the one-build entry point. Run: `python tracer_bullet.py`. |
| `worker.py` | The production worker. Claims a queued build with `FOR UPDATE SKIP LOCKED` (correct multi-worker claim, no double-processing), runs the same `DagExecutor` against a durable CAS root and a `PostgresCacheStore`, and records `build_stages` / `artifacts` rows. **U1 durability:** claims carry `attempt` / `worker_id` / `lease_expires_at`; a row left `running` by a killed worker is reclaimed when its lease expires; a poison build dead-letters after `MAX_ATTEMPTS`; a build is marked `failed` before an unexpected exception is re-raised, so the process dies loudly but the row is never stuck. **U2:** `_initial_inputs_for` maps `document_id` → `manuscripts.source_sha256` → CAS bytes → the `ingest` stage; **a document with no stored source is a `BAD_INPUT` failure, never a silent fixture substitution.** Also emits `NOTIFY build_<id>` for the API's SSE route, and logs JSON. |
| `cli.py` | `pub`-style dev/admin CLI: `run-stage <name> [--fixture <v>]`, `cas put <file>`, `cas get <hash>`, `corpus generate [--templates ...]`, `schema validate <file>`, `schema gen`. |
| `conftest.py` | Puts the repo root on `sys.path` for the whole test run, so `profiles`, `stages`, `templates`, `tracer_bullet` (plain directories, not installed distributions) import under pytest. The `publisher_*` packages are already installed. |
| `docker-compose.yml` | `postgres` + `worker` + `api`. **Postgres publishes 55432, not 5432** — a locally installed Postgres commonly owns 5432, and when it does, the host-side integration tests silently connect to *that* database instead. `platform/db/schema.sql` is mounted into `docker-entrypoint-initdb.d`. Both worker and api mount the shared `cas-data` volume **read-write** (the API writes uploads). `PYTHONUNBUFFERED=1` on the worker, or `docker compose logs worker` stays empty for a whole build. |
| `Dockerfile.worker` | python:3.12-slim + weasyprint's real Cairo/Pango stack **and ghostscript**. Both are load-bearing, not optional: the worker runs `allow_stub_engines=False`, so without them `finish` refuses to run rather than certify an unconverted PDF. Copies `platform/ services/ stages/ schemas/ profiles/ templates/ corpus/` onto one PYTHONPATH — which is why `tools/lint_service_deps.py` exists. |
| `Dockerfile.api` | node:22-slim, two-stage build. **Preserves the `repo-root/packages/api/` layout** because `packages/api/tsconfig.json` extends the monorepo root — flattening it makes `../../tsconfig.json` resolve to nothing. |
| `requirements.txt` | The only place third-party Python deps are actually pinned (every package's `pyproject.toml` declares `dependencies = []` by pre-existing convention). Notable pins: `weasyprint>=62,<63` with **`pydyf<0.11`** (weasyprint 62.x calls a Stream API pydyf 0.11+ removed), `psycopg2-binary`, `PyYAML`, `jsonschema`, `python-docx` (**legacy binary `.doc` is not supported — convert with LibreOffice first**). |
| `pyproject.toml` | pytest config only. `testpaths = [platform, services, packages, stages, tests]` — `tests` was once omitted, so a bare pytest never ran the contract tests or the collection floor. `norecursedirs` keeps collection out of `node_modules` and the sharded CAS directories. |
| `package.json` | Root workspace scripts: `gen`, `gen:check`, `test`, `lint`, `build` (all `pnpm -r`). Node ≥ 22. |
| `pnpm-workspace.yaml` | Workspaces: `schemas/*`, `platform/*`, `packages/*`, `services/*`, `infra/*`. |
| `Cargo.toml` / `Cargo.lock` | Rust workspace: `platform/cas`, `platform/pagescan`. |
| `tsconfig.json` | Extended by `packages/api` and the three `platform/*/ts` configs. **`packages/web` deliberately does not extend it** — Next 15 needs `module: esnext` / `moduleResolution: bundler` / `jsx: preserve` / `noEmit`, all incompatible with this file's `Node16` + `outDir: dist`. Do not "fix" that inconsistency. |
| `.npmrc` | pnpm settings. `package-lock.json` / `yarn.lock` are gitignored — an npm/yarn lockfile here would pin a second, conflicting dependency graph. |
| `README.md` | Public overview + the per-module status table. |
| `CLAUDE.md` | Project instructions **and the folder/skill navigation map** — start there. |
| `implementation_audit_report.md` | 2026-08-11 independent audit of the Stage 1–2 uplift (commits `7a4401b..beeff69`). Distinct from the older `docs/implementation_audit_report.md` (2026-07-29). |
| `.github/workflows/ci.yml` | Three jobs — `python` (full suite + schema lint), `rust` (`cargo test`, `clippy -D warnings`), `contracts` (codegen in sync, generated types execute, **DAG integrity**, generated contract tests, version-bump lint, service-deps lint). Every gate here previously carried `continue-on-error: true`, so all of them were advisory; two could not have failed anyway. They are enforced now — do not re-add `continue-on-error`. |
| `.reasonix/` | Four small JSON files of desktop-tool topic metadata (titles, created-at, title sources). **Not source, not build state** — nothing in the pipeline reads them. Ignore unless explicitly asked. |
| `.gitignore` | Note the two non-obvious groups: `*.gen.*` — its comment claims "checked in", which is **false**: they are ignored, zero are tracked, and `gen:check` therefore cannot fail (see **publisher-schemas**); and `.publisher/` / `.test-cas-cache/` / `.cas-cache/` (durable CAS blobs + sqlite cache index; content-addressed output, not source). |

## Commands

```bash
./scripts/test.ps1              # ALWAYS this, never bare pytest
python tracer_bullet.py         # full pipeline, stubs allowed
docker compose up -d            # postgres + worker + api  (API on :4000, PG on :55432)
python cli.py schema validate <file>
python platform/stages/integrity.py
```

Cap tool output — pipe builds, pulls and installs through `tail`. A single
`docker compose pull` log is 1,200+ lines of progress noise.

## Related

Every `publisher-*` skill. The map of which folder to open for which task lives in
`CLAUDE.md`.
