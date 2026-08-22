# Publisher

DOCX manuscript → print-ready book PDF. Python pipeline, TypeScript API, Rust helpers.

**This file exists partly to displace a wrong one:** `E:\Documents\Vibe-Coding\CLAUDE.md`
described *weebot* (a different project, which has its own `weebot/CLAUDE.md`) and was
being injected into every session here.

## Where things live — folder map and skills

Every top-level folder has a skill under `.claude/skills/` describing what it contains and
what each file does. **Load the skill for a folder before editing anything in it** — each one
carries the invariants that folder enforces and the mistakes that have already been made there.

| Folder | Skill | What lives there |
|---|---|---|
| `stages/` | **publisher-stages** | The 17 `@stage(...)` declarations. **Not one module per stage** — `prepress_stages.py` holds `preflight`/`cover`/`cover-preflight`/`finish-gs`, `cover_stages.py` holds the four `cover-brief/art/judge/compose`, and `ast-assemble` lives in `structure_stage.py`. The DAG is derived from these. |
| `platform/` | **publisher-platform** | The substrate: CAS, build cache, the stage registry + DAG integrity checker, sandbox, `db/schema.sql`, `routing/policy.yaml`, the Rust crates (cas, pagescan), reproducibility, supply-chain. |
| `services/` | **publisher-services** | The domain logic stages call into: ingest (DOCX→AST), structure (rules/inference/overrides), prepress (geometry, fontvault, ghostscript, preflight), cover, epub, idml, onix, alttext, agents. |
| `schemas/` | **publisher-schemas** | JSON Schema source of truth + the Pydantic/Zod codegen. A schema ID is an API. |
| `packages/` | **publisher-packages** | TypeScript surfaces: `api` (Fastify + Postgres REST) and `web` (Next.js review UI). Owns no pipeline logic. |
| `profiles/` | **publisher-profiles** | Vendor output profiles as YAML data (KDP, IngramSpark, Lulu, generic, Greek) + the loader. |
| `templates/` | **publisher-templates** | DesignSpec presets — the starter book designs. |
| `fixtures/` | **publisher-fixtures** | Versioned per-stage fixture sets the generated contract tests run over. |
| `corpus/` | **publisher-corpus** | Golden + synthetic manuscripts and the raster-diff harness. |
| `tests/` | **publisher-tests** | Contract tests, collection floor, Postgres-backed integration suite. Also maps the co-located unit tests. |
| `tools/` | **publisher-tools** | The three CI-blocking lints (schema free-text, stage version bump, service deps). |
| `scripts/` | **publisher-scripts** | `test.ps1` / `test.sh` — the test runners you must use instead of bare pytest. |
| `docs/` | **publisher-docs** | Architecture, build plan (D1–D10), remediation/uplift plans and their status, cover design, LLM strategy, agent design, audits. |
| repo root | **publisher-root** | `tracer_bullet.py`, `worker.py`, `cli.py`, `conftest.py`, Docker/compose, dependency manifests, `.github/workflows/ci.yml`, `.reasonix/`. |

### Go here for this task

| Task | Start at |
|---|---|
| Add or change a build step | Find the module with `grep -n 'name="<stage>"' stages/*.py` (names ≠ filenames) → bump `@stage(version=)` → add new modules to `stages/__init__.py` only (`integrity.py` does `import stages`; it has no list, and re-adding one recreates a duplicate that already drifted once) |
| Change what a stage consumes/produces | The `inputs=`/`outputs=` schema IDs in `stages/` — the DAG follows automatically |
| A stage is unreachable | Fix the producing chain. Never promote an input to `root_inputs`, never set `allow_stub_engines` |
| Add or edit an artifact schema | `schemas/<name>/` → `cd schemas && node codegen/generate.mjs` → `node codegen/test.mjs`. The `.gen.*` files are **gitignored and untracked** — do not try to commit them, and note `gen:check` cannot fail (see **publisher-schemas**) |
| DOCX parsing / text loss | `services/ingest/publisher_ingest/docx_to_ast.py` |
| Chapter/front-matter detection, confidence | `services/structure/publisher_structure/rules.py` |
| LLM routing, model choice, cache keys | `platform/routing/policy.yaml` + `services/structure/publisher_structure/inference.py` |
| Human/agent edits to a book | `services/structure/publisher_structure/overrides.py` (never mutate the AST) |
| Trim, bleed, spine, gutter | `services/prepress/publisher_prepress/geometry.py` + the vendor file in `profiles/` |
| PDF/X conversion, proof PDFs | `services/prepress/publisher_prepress/ghostscript.py` (one impl, shared by both finish stages) |
| Preflight rules / delivery gate | `services/prepress/publisher_prepress/preflight.py` |
| Fonts and licensing | `services/prepress/publisher_prepress/fontvault.py` |
| Book typography defaults | `templates/__init__.py` (+ the matching `profiles/` entry) |
| Cover art, models, judging | `services/cover/publisher_cover/` + `stages/cover_stages.py` (but the `cover` geometry and `cover-preflight` stages are in `stages/prepress_stages.py`) |
| EPUB / IDML / ONIX output | `services/epub/`, `services/idml/`, `services/onix/` |
| Agent behaviour and limits | `services/agents/publisher_agents/runtime.py` |
| HTTP route, auth, tenancy, uploads | `packages/api/src/routes/` + `src/plugins.ts` + `src/db.ts` |
| Review UI | `packages/web/src/review/` |
| Queue, leases, retries, dead-letter | `worker.py` + `platform/db/schema.sql` |
| Run one build locally | `python tracer_bullet.py` |
| A test failed / CI is red | **publisher-tests**, then `tools/` for the lints |
| Why is it built this way | `docs/` — cite sections by identifier (D8, A3, U5, O1, S1) |

## Architecture — the load-bearing ideas

**The DAG is derived, never hand-wired.** Stages declare themselves with `@stage(...)`
in `stages/*.py`; the executor matches one stage's output schema ID to another's input
schema ID to build the graph. Adding an edge means changing a *declaration*, not wiring.
`platform/stages/py/publisher_stages/__init__.py` owns the registry.

**Reachability is a fixpoint.** A build runs only stages whose root inputs are supplied
(or declared `optional_root_inputs`) and whose non-root inputs some other reachable stage
produces. This is why `import stages` can register the entire cover pipeline without a
book build attempting it.

**Content-addressed storage.** Artifacts are sha256-keyed blobs under `PUBLISHER_CAS_ROOT`
(default `./.publisher/cas`), sharded `h[:2]/h[2:4]/h`. Stages write via `ctx.cas_root` —
never `ctx.work_dir`, which is scratch and is deleted.

**Two hard gates. Neither may be softened.**
- *Text integrity* (`ast-assemble`): `normalize(text(html)) == normalize(text(source))`.
  Fails the build outright. No override flag exists, deliberately.
- *Preflight* (`preflight`): `package` declares `preflight_report` as a required input, so
  a build physically cannot be packaged without a preflight verdict.

**`allow_stub_engines` is a dev-only escape hatch.** Default `False`. The worker never sets
it. It exists so a missing renderer fails loudly instead of silently certifying stub output
as press-ready. If a chain is unreachable, fix the chain — do not promote an input to root
and do not set this flag to route around a gate.

**Execution tiers.** `tracer_bullet.py` is the local dev harness (one build, stdout,
stubs allowed). `worker.py` is production: claims builds from Postgres with
`FOR UPDATE SKIP LOCKED`, real engines only. `packages/api` is Fastify + Postgres and
owns no pipeline logic.

## Commands

```bash
./scripts/test.ps1          # ALWAYS use this, not bare pytest (see below)
python tracer_bullet.py     # full pipeline, stubs allowed
docker compose up -d        # postgres + worker + api
python cli.py schema validate <file>
```

**Never run bare `pytest`.** Results were being read from a machine-global,
cross-project log directory and a sibling repo's failures got reported as Publisher's.
`scripts/test.ps1` writes to `.publisher/test-output.txt` and disables plugin autoload
(~160s → ~14s). See `docs/COST_AND_STABILITY_PLAN.md` §S1.

## Working here

- Cap tool output: pipe builds/pulls/installs through `tail`. A single `docker compose pull`
  log is 1,200+ lines of progress noise.
- Prefer `Grep`/targeted `Read` over re-reading large files already seen.
- Don't switch models mid-task — it voids the prompt cache and re-bills the whole context.
- Load the folder's skill (table above) before editing in it, rather than reading the whole
  folder to work out what is there.

## Docs

`docs/ARCHITECTURE.md` · `BUILD_PLAN.md` (phases, decisions D1–D10) ·
`ARCHITECTURE_REMEDIATION.md` (A0–A2 landed, A3–A5 open) ·
`ARCHITECTURE_UPLIFT_PLAN.md` (U1–U4 implemented+tested, U5–U7 implemented, U8–U9 open) ·
`BLOCKING_FIX_PLAN.md` (D1–D3 landed in c5cfad3) ·
`COST_AND_STABILITY_PLAN.md` · `REMEDIATION_PLAN.md` (complete) ·
`VERIFICATION_PLAN.md` (G1–G8 — gates that cannot fail; **draft, nothing implemented**)

Full index with per-document status: the **publisher-docs** skill.
