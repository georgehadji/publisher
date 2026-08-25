# Publisher

DOCX manuscript → print-ready book PDF. Python pipeline, TypeScript API, Rust helpers.

**This file exists partly to displace a wrong one:** `E:\Documents\Vibe-Coding\CLAUDE.md`
described *weebot* (a different project, which has its own `weebot/CLAUDE.md`) and was
being injected into every session here.

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

**Two render paths, one DAG.** `PUBLISHER_RENDER_ENGINE` (`css` default, or `typst`)
selects which pair of alternative stages the graph binds: `design-compile` + `paginate`
(weasyprint) or `design-compile-typst` + `paginate-typst` (pandoc → Typst). Selection is
registry data, not a branch in the executor. Both consume `doc-effective/1`, so neither
can reach a PDF without the integrity gate. Bleed differs by necessity: weasyprint declares
it with CSS `bleed:`, Typst lays out at trim+2×bleed and lets `finish-gs` inset the
TrimBox — read `stages/typst_stages.py`'s module docstring before changing either.

**The IDML deliverable is terminal on purpose.** `PUBLISHER_EMIT_IDML=1` registers the
`idml` stage (pandoc ICML → a validated IDML package). InDesign composes text at open
time, so this stage cannot know the page count: it takes the render path's measured
pagemap as a frame-count *hint* and lets Smart Text Reflow correct it. Never let anything
consume `idml/1`, and never derive a spine or a preflight verdict from it.

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

## Docs

`docs/ARCHITECTURE.md` · `BUILD_PLAN.md` (phases, decisions D1–D10) ·
`ARCHITECTURE_REMEDIATION.md` (A0–A2 landed, A3–A5 open) ·
`COST_AND_STABILITY_PLAN.md` · `REMEDIATION_PLAN.md` (complete)
