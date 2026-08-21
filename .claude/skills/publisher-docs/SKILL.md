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
| `ARCHITECTURE.md` | **The primary reference.** §1.2 is the canonical stage list; §2.5 caching, §2.6 overrides + geometry, §2.7 DesignSpec emitters, §2.8 stage declarations and the error taxonomy, §2.9 sandbox, §2.10 font licensing, §2.15 the CI-blocking gate list, §3.2 CAS types. |
| `BUILD_PLAN.md` | v2.1, ~1000 lines. §0 non-functional targets (gate-enforced), §1 engineering doctrine **D1–D10**, §3 per-subsystem plans (§3.3 cache/version rule, §3.4 sandbox, §3.10 prepress, §3.11 IDML, §3.12 EPUB, §3.13 pagescan, §3.15 ONIX, §3.16 structure/inference, §3.17 overrides + review, §3.18 alt-text, §3.20 contract tests), §5 phases P0–P6, §8 corpus. |
| `RESEARCH.md` | The market and technology research behind the product shape. Verdict: **not** "DOCX → InDesign", but DOCX → canonical semantic book model → N renderers, with InDesign as one renderer. |
| `AGENT_DESIGN.md` | What makes an agent safe here: action space = override log + DesignSpec patches only; agents run at gate boundaries, never inside deterministic stages; every action attributable and reversible. Defines Structure Wrangler, Compositor, Preflight Explainer, Manuscript Doctor. |
| `LLM_STRATEGY.md` | §0 the one architectural rule (no LLM output in a deterministic stage), §1 where an LLM adds value, §2 where it must never appear, §3 closed-enum discipline (§3.16/§3.18 on why alt-text is a separate service), §4 routing policy is data, not code. |
| `COVER_DESIGN.md` | The cover pipeline: §0–§1 the stage graph and the page-count-free split, §2 ArtBrief, §3 model catalogue and dispatch, §4 the images endpoint, §5 no image model renders type, §8 the judge gates, §9 provenance in the delivery package, §10 the ImageGenPort seam, §11 cache-key discipline. |
| `PRODUCTION_READINESS.md` | Why the bar is higher than normal SaaS: correctness is adjudicated by a third party weeks later at cost, and the input is irreplaceable. |
| `PARALLELIZATION.md` | What can actually be worked in parallel. The floor is set by external feedback loops (vendor round-trips, physical proofs at 1–2 weeks per cycle), not by code volume. |
| `OPTIMIZATION.md` | O-series optimizations. **O1** (Typst as default renderer, ~1/10 memory and ~1/5 time vs Chrome — collapses most of the pool/admission infrastructure), **O8** (synthetic corpus hedge). |

## Plans with a status (check before assuming)

| File | Status |
|---|---|
| `REMEDIATION_PLAN.md` | **Complete.** Closed the correctness gaps — gates that could not fail, a DAG that bypassed them, missing enforcement. §F5 deferred four items. |
| `ARCHITECTURE_REMEDIATION.md` | **A0–A2 landed, A3–A5 open.** Picks up two of the four F5 deferrals (CacheStore backend, real execution wiring) because they were one missing subsystem, not independent. Real Ghostscript and the real Typst emitter were correctly deferred and out of its scope. |
| `ARCHITECTURE_UPLIFT_PLAN.md` | 6.0 → >8.5. **Stage 1 (U1–U4) implemented and tested as of 2026-08-10.** U1 worker durability, U2 real manuscripts, U3 service deps, U5 API hardening, U6 route split, U7 observability. Continues `ARCHITECTURE_REMEDIATION.md`. |
| `BLOCKING_FIX_PLAN.md` | **Draft, awaiting implementation** (2026-08-11). D1/D2/D3 from `implementation_audit_report.md` §7, verified against live code. |
| `COST_AND_STABILITY_PLAN.md` | §1 outstanding correctness problems (**§P3** is the serious one: a wrong conclusion reported from a repo never under test), §2 where the tokens go, **§S1** the test-runner fix. |
| `implementation_audit_report.md` (in `docs/`) | 2026-07-29 full-codebase audit against BUILD_PLAN v2.1. Verdict: approved with changes. |

Note: the repo root also has an `implementation_audit_report.md` — a **different, later**
document (2026-08-11, audit of the Stage 1–2 uplift over commits `7a4401b..beeff69`). The two
are not duplicates; check the date line before citing either.

## Rules that bite

- **Check status before acting on a plan.** A3–A5 are open; U1–U4 are landed; BLOCKING_FIX_PLAN
  is a draft. Treating an open item as done (or vice versa) is the common mistake here.
- **Doc sections are load-bearing in code comments.** If you change a numbered decision,
  grep for the identifier (`grep -rn "D8\|§3.20" --include=*.py`) — stages cite them by number.
- **Do not soften a documented gate to make progress.** The two hard gates (text integrity in
  `ast-assemble`, preflight before `package`) have no override flag by design.

## Related

Root `README.md` (module status table) · root `CLAUDE.md` (the navigation map) · every other
`publisher-*` skill cites sections from here.
