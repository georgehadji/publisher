---
name: publisher-docs
description: Map of the `docs/` folder — architecture, build plan and decisions D1–D10, remediation and uplift plans, cost/stability, cover design, LLM strategy, agent design, optimization, parallelization, production readiness, research, and the audit reports. Use this whenever a task needs the rationale behind a design, a plan's status (landed vs open), a decision reference like D8 / A3 / U5 / O1 / P0, or asks "why is it built this way". Read before making an architectural change or citing a doc section.
---

# `docs/` — the rationale and the plans

The code answers *what*; this folder answers *why*, and records what is landed versus open.
Cite sections by their identifiers (`D8`, `A3`, `U5`, `O1`, `S1`, `P0`) — the code comments
already do.

## Reference documents (stable)

| File | What it is |
|---|---|
| `ARCHITECTURE.md` | **The primary reference.** §1.2 is the canonical stage list; §2.5 caching, §2.6 overrides + geometry, §2.7 DesignSpec emitters, §2.8 stage declarations and the error taxonomy, §2.9 sandbox, §2.10 font licensing, §2.13 storage layout (the CAS sharding), §2.15 Testing (the CI-blocking gate table). **Part 3 has no numbered subsections** — do not cite `ARCHITECTURE.md §3.x`; the `§3.2 CAS` reference people reach for is in BUILD_PLAN, not here. |
| `BUILD_PLAN.md` | v2.1, ~1000 lines. §0 non-functional targets (gate-enforced), §1 engineering doctrine **D1–D10**, §3 per-subsystem plans — verified headings: §3.1 schemas, §3.2 CAS, §3.3 cache, §3.4 sandbox, §3.5 ingest, §3.6 structure, §3.7 overrides, §3.8 design/DesignSpec, §3.9 pagination (**and pagescan — it has no section of its own**), §3.10 prepress, §3.11 IDML **and** EPUB together, §3.12 API, §3.13 orchestrator, §3.14 review UI, §3.15 error taxonomy, §3.16 inference gateway, §3.17 agents, §3.18 alt-text, §3.19 learning, §3.20 stage registry. **ONIX has no §3.x section** (P6 only). §5 phases, §8 corpus. |
| `RESEARCH.md` | The market and technology research behind the product shape. Verdict: **not** "DOCX → InDesign", but DOCX → canonical semantic book model → N renderers, with InDesign as one renderer. |
| `AGENT_DESIGN.md` | What makes an agent safe here: action space = override log + DesignSpec patches only; agents run at gate boundaries, never inside deterministic stages; every action attributable and reversible. Defines Structure Wrangler, Compositor, Preflight Explainer, Manuscript Doctor. |
| `LLM_STRATEGY.md` | §0 the one architectural rule (no LLM output in a deterministic stage), §1 where an LLM adds value, §2 where it must never appear, §3 "Prose contamination: make it unrepresentable, don't check for it", §4 routing policy is data not code, §5 cost model, §8 the three that matter. (The alt-text-is-a-separate-service rationale is **BUILD_PLAN** §3.16/§3.18 — this file has no §3.x subsections.) |
| `COVER_DESIGN.md` | The cover pipeline: §0–§1 the stage graph and the page-count-free split, §2 ArtBrief, §3 model catalogue and dispatch, §4 the images endpoint, §5 no image model renders type, §8 the judge gates, §9 provenance in the delivery package, §10 the ImageGenPort seam, §11 cache-key discipline. |
| `PRODUCTION_READINESS.md` | Why the bar is higher than normal SaaS: correctness is adjudicated by a third party weeks later at cost, and the input is irreplaceable. |
| `PARALLELIZATION.md` | What can actually be worked in parallel. The floor is set by external feedback loops (vendor round-trips, physical proofs at 1–2 weeks per cycle), not by code volume. |
| `OPTIMIZATION.md` | O-series optimizations. **O1** (Typst as default renderer, ~1/10 memory and ~1/5 time vs Chrome — collapses most of the pool/admission infrastructure), **O8** (synthetic corpus hedge). |

## Plans with a status (check before assuming)

| File | Status |
|---|---|
| `REMEDIATION_PLAN.md` | **Complete.** Closed the correctness gaps — gates that could not fail, a DAG that bypassed them, missing enforcement. §F5 deferred four items. |
| `ARCHITECTURE_REMEDIATION.md` | **A0–A2 landed, A3–A5 open.** Picks up two of the four F5 deferrals (CacheStore backend, real execution wiring) because they were one missing subsystem, not independent. Real Ghostscript and the real Typst emitter were correctly deferred and out of its scope. |
| `ARCHITECTURE_UPLIFT_PLAN.md` | 6.0 → >8.5, owns **U1–U9**. Two different strength claims, both as of 2026-08-10: **Stage 1 (U1–U4) implemented *and tested*** — U1 worker durability, U2 real manuscripts, U3 service deps, **U4 resolve the AI layer**; **Stage 2 (U5–U7) implemented but not audit-reviewed** — U5 API hardening, U6 boundary hygiene (the route split is one of six bullets), U7 observability. **U8–U9 (bounded concurrency, prove under load) remain open** — they are the only open items. Continues `ARCHITECTURE_REMEDIATION.md`. |
| `BLOCKING_FIX_PLAN.md` | Its own header still says "draft — awaiting implementation", but that was **stale on arrival**: D1/D2/D3 shipped in the same and only commit that added the doc (`c5cfad3 fix: resolve the three blocking audit findings (D1, D2, D3)`). Treat D1–D3 as landed and verify against code before re-implementing. |
| `COST_AND_STABILITY_PLAN.md` | §1 outstanding correctness problems (**§P3** is the serious one: a wrong conclusion reported from a repo never under test), §2 where the tokens go, **§S1** the test-runner fix. |
| `VERIFICATION_PLAN.md` | **Draft — nothing implemented** (2026-08-22). Owns **G1–G8**. The gates that cannot fail: CI never installs pytest (red since 2026-08-11), `gen:check` exits 0 on ignored files, `lint_stage_versions --base` silently passes, nothing validates `.claude/skills/`. Plus deriving the skill tables, correcting the stale source comments they were copied from, reconciling three folder maps, and the always-loaded context budget. |
| `CONTEXT_ARCHITECTURE.md` | **Research memo — nothing implemented** (2026-08-24). Owns **C1–C4**. Evaluates the Interpretable Context Methodology against this repo: L0/L1 already satisfied by `CLAUDE.md`, L2 already satisfied *and better* by `StageDeclaration` (adopting prose stage contracts would regress — §3), resident context already inside ICM's token band (§2). What survives: **C1** give `VERIFICATION_PLAN` G5's generator an Inputs/Process/Outputs shape, **C2** state that `profiles/`/`templates/`/`schemas/` are never-invent reference data, **C3** lint the `cas_root` vs `work_dir` boundary. **C4** records what was declined. |
| `implementation_audit_report.md` (in `docs/`) | 2026-07-29 full-codebase audit against BUILD_PLAN v2.1. Verdict: approved with changes. |

Note: the repo root also has an `implementation_audit_report.md` — a **different, later**
document (2026-08-11, audit of the Stage 1–2 uplift over commits `7a4401b..beeff69`). The two
are not duplicates; check the date line before citing either.

## Rules that bite

- **Check status before acting on a plan — and check the code, not just the header.**
  A3–A5 open; U1–U4 landed and tested; U5–U7 landed but not audit-reviewed; **U8–U9 open**;
  BLOCKING_FIX_PLAN's "draft" header is stale (D1–D3 landed with it). Treating an open item as
  done, or a done one as open, is the common mistake here — this table got it wrong twice
  before being corrected.
- **Doc sections are load-bearing in code comments.** If you change a numbered decision,
  grep for the identifier (`grep -rn "D8\|§3.20" --include=*.py`) — stages cite them by number.
- **Do not soften a documented gate to make progress.** The two hard gates (text integrity in
  `ast-assemble`, preflight before `package`) have no override flag by design.

## Related

Root `README.md` (module status table) · root `CLAUDE.md` (the navigation map) · every other
`publisher-*` skill cites sections from here.
