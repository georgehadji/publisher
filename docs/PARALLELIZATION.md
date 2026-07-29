# Publisher — Parallelization Plan

Can [BUILD_PLAN.md](BUILD_PLAN.md)'s ~26 weeks be compressed? Yes — substantially. But the compression comes from a different place than people expect.

---

## 0. Verdict

| Team | Wall-clock to GA | Notes |
|---|---|---|
| 1–2 engineers | **26 weeks** | Parallelization doesn't help. Reorder for risk instead (§7) |
| 3 engineers | **~20 weeks** | 4 tracks; coordination still cheap |
| **5 engineers + contract typographer** | **~16 weeks** | Recommended. ~38 % compression |
| 8+ engineers | **~14 weeks** | Diminishing; coordination overhead dominates |
| Unlimited | **~13 weeks floor** | Unreachable in practice |

**The floor is not set by code volume. It's set by feedback loops with external latency**: typographic quality iteration, vendor submission round-trips, physical proof turnaround (order → print → ship → review is 1–2 weeks *per cycle*), and pentest scheduling. No amount of engineers compresses those.

Which means the highest-value optimization is not "add people to the build." It is:

> **Start every slow feedback loop in week 3 with a deliberately bad artifact, instead of in week 12 with a good one.**

---

## 1. The dependency graph — real vs assumed

BUILD_PLAN v2 presents P0→P9 sequentially. Most of that ordering is narrative, not causal. Actual dependencies:

```
schemas ──┬──────────────────────────────────────────────────► (everything)
          │
stage contract + CAS + cache ──┬────────────────────────────► orchestrator, api
                               │
                               ├─► ingest ─────► [typescript fixtures]
                               │                       │
                               ├─► structure ◄─────────┘
                               │       │
                               │       └──► overrides ──────► learning*
                               │
                               ├─► design (DesignSpec only) ─► emitters ──┐
                               │                                          ▼
                               ├─► composition ◄── [EffectiveDoc fixtures]┘
                               │       │
                               │       └─► pagescan ──────► Compositor agent
                               │
                               ├─► prepress ◄── [fixture PDFs]   ★ NO upstream dep
                               │
                               ├─► idml / epub / alttext ◄── [AST fixtures]
                               │
                               └─► api / web ◄── [API contract]

*learning needs a clean override log to exist; it can be BUILT against synthetic override data
```

### The three dependencies that are real

1. **`schemas` → everything.** Genuine, and short (~3 days). Everything imports generated types.
2. **`pagescan` (deterministic defect scan) → Compositor agent.** Genuine and stated in the plan: an agent papering over a weak scanner produces plausible fixes for defects you should have detected exactly, and you'll never know which is which.
3. **Templates (typographer) → the P3 blind A/B gate.** Genuine — but templates are a *design* track that can start week 1, so this only blocks if you staff it late.

### The three that are assumed and aren't

1. **`prepress` does not depend on `composition`.** It takes a PDF. *Any* PDF. Build the entire PDF/X + ICC + geometry + preflight stack against fixture PDFs — hand-made, exported from InDesign, whatever — starting week 1. This is the **single biggest parallelization win in the plan**: it moves ~3 weeks of critical path to week 1.
2. **`structure` does not depend on `ingest`.** It takes typescript HTML. Hand-write 20 fixtures in a day and build the entire rules + inference + override + rebase stack against them.
3. **`cover` does not depend on a real interior at build time.** The *runtime* dependency (final page count → spine) is real, but the module takes a page count as a parameter. Build it with fixture page counts.

---

## 2. The three enablers

Parallelism here isn't a scheduling trick — the architecture already provides it, and the plan just failed to exploit it.

**E1 — Schema-first, week 1.** Write *all* artifact schemas in week 1, not just AST: `typescript`, `ast`, `overrides`, `designspec`, `profile`, `pagemap`, `preflight`, `manifest`, `classification`, `agent-proposal`. Codegen both languages. Every track then codes against generated types.

**E2 — Fixture-driven development.** Every stage is `f(inputs, params, toolchain) → artifacts` with declared input and output schemas. That means every stage can be developed against *fixture inputs* and validated by *contract tests on its outputs*, with zero knowledge of its neighbours. Fixtures live versioned in the corpus repo; downstream tracks pin a fixture version.

**E3 — Tracer bullet before fan-out.** Weeks 2–3: one thin, ugly, end-to-end slice through **every** stage — one manuscript, one template, no compliance, no quality. Its only job is to prove the contracts are right before five people build against them for six weeks.

> **Never fan out before the tracer bullet passes.** The failure mode of fixture-driven parallelism is five tracks building perfectly against contracts that turn out to be wrong.

---

## 3. Track structure

Recommended: 5 engineers + 1 contract typographer + fractional ops/legal.

| Track | Owner | Owns | Blocked by | Free from |
|---|---|---|---|---|
| **T1 Platform** | Eng 1 | schemas, CAS, cache key, stage contract, sandbox, DAG executor → Temporal, observability, supply chain | — | W1 |
| **T2 Ingest + writers** | Eng 2 (Py) | sanitizers, LibreOffice, `docxstream`, XSweet, media; later IDML/EPUB/alt-text/ONIX | schemas | W2 |
| **T3 Structure + inference** | Eng 3 (Py) | rules, AST assembly, integrity gate, `services/inference`, overrides, rebase; later agents + learning | schemas + typescript fixtures | W2 |
| **T4 Design + composition** | Eng 4 (TS) | DesignSpec, 3 emitters, Chrome pool, Paged.js, pagemap, `pagescan`, fixpoint | schemas + EffectiveDoc fixtures | W2 |
| **T5 Prepress + delivery** | Eng 5 (Py/Rust) | Ghostscript/ICC, geometry, `pdfprobe`, preflight rules, vendor profiles, cover, packaging | **fixture PDFs only** | **W1** |
| **T6 Product surface** | Eng 1 + 4 (shared) | API, orchestrator wiring, review UI, instrumentation | schemas + API contract | W3 |
| **TD Design** | Typographer + designer | 8 templates, DesignSpec authoring, type pairings, ornaments | DesignSpec schema | **W1** |
| **TB Business** | Founder/ops | corpus licensing, vendor accounts, legal, insurance, pentest booking | — | **W1** |

Track count should not exceed team size. With 3 engineers, merge T2+T5 and T4+T6. With 8, split T3 into inference/agents and structure, and give T6 its own owner.

---

## 4. Pull-left: the optimization that actually matters

External-latency items don't parallelize — so start them before you're ready, deliberately.

| Loop | Plan v2 | Pull left to | What you learn early |
|---|---|---|---|
| **Physical POD proof** | ~W12 | **W3** | Paper stock reality, gutter feel, ink density, account friction, real turnaround. Each cycle is 1–2 weeks; you want 4+ cycles before GA, not 1 |
| **IngramSpark submission** | P1 gate (~W10) | **W4** | Their actual rejection messages, which are the spec you're really building to. Submit a knowingly imperfect file |
| **KDP submission** | P1 gate | **W4** | Same |
| **Typographer engaged** | P3 (~W12) | **W1** | Template design has a long lead; also they'll tell you in week 2 what your DesignSpec is missing |
| **Golden corpus licensing** | W1 (already) | **W1** | Blocks every correctness gate. Outreach is slow |
| **Pentest booking** | ~W20 | **W6** | 6–8 week lead time is normal; remediation needs slack after |
| **E&O insurance quote** | pre-GA | **W8** | Underwriting asks questions you'll need engineering answers for |
| **Blind A/B panel recruitment** | P3 gate | **W8** | You need real readers/designers lined up before the gate, not after |

Submitting a bad file to IngramSpark in week 4 costs a few dollars and teaches you more about the P1 gate than four weeks of reading their PDF guide. That is the entire point.

---

## 5. Schedule — 5 engineers

```
W1   ████ ALL: schemas (3d) → contracts frozen v0.1
     T1: CAS + cache key    T5: fixture PDFs + Ghostscript spike
     TD: DesignSpec authoring   TB: corpus, typographer, vendor accounts

W2–3 ████ TRACER BULLET (all hands, thin slice, one template, ugly)
     ★ W3: first physical POD proof ordered

W4–9 ████ PARALLEL BUILD-OUT
     T1  sandbox · DAG exec · observability · supply chain
     T2  ingest streaming · sanitizer chain · media
     T3  rules · integrity gate · inference gateway · overrides · rebase
     T4  emitters · Chrome pool · pagemap · pagescan · fixpoint
     T5  PDF/X · geometry · preflight rules · vendor profiles · cover
     T6  API · orchestrator · review UI + instrumentation
     TD  templates 1–4
     ★ W4: first deliberate vendor submissions   ★ W6: pentest booked
     ★ W7: second POD proof (from the tracer output)

W10–11 ██ INTEGRATION + COMPLIANCE GATE
     All tracks converge. Real vendor submissions on real output.
     Gate: IngramSpark accepts first upload; 5 submissions, 0 rejections

W12–15 ██ QUALITY LOOP (irreducible)
     T3+T4+TD: typographic iteration, templates 5–8, raster-diff harness
     T1+T5: hardening, soak prep    T2: IDML/EPUB/alt-text
     ★ W13, W15: POD proofs
     Gate (W15): blind A/B vs a $900 human typesetter

W16–17 ██ AGENTS  (needs pagescan, done ~W11)
     T3: Structure Wrangler · Compositor crop+verify · Preflight Explainer
     Gate: ≥70% proposals accepted unedited; review 40min → ≤5min

W18–19 ██ HARDEN + SECONDARY
     T1: 1000-book soak · restore drill · DR    T5: reproducibility nightly
     T2: EPUB a11y gates · ONIX    Pentest runs; remediation
     T3: learning built against synthetic override data (decoupled)

W20–21 ██ LEARNING + PLATFORM
     T3: memory · consolidation · judge calibration · holdout · promotion CI
     T6: public API · webhooks · billing · quotas · escape hatch

W22    ██ GA sign-off
```

**~16 weeks of parallel work + integration and gate weeks ≈ W22 calendar**, against 26 sequential. The compression is ~38 %, and roughly half of it comes from T5 starting in week 1 and the pulled-left feedback loops.

### Milestone dependency barriers (hard)

```
W1   contracts frozen         → all tracks unblocked
W3   tracer bullet green      → fan-out permitted
W11  integration green        → compliance gate
W11  pagescan done            → agent track may start
W15  A/B gate passed          → agents allowed to touch composition
W19  soak + reproducibility   → GA candidate
W21  pentest remediated       → GA
```

---

## 6. What must NOT be parallelized

Splitting these makes them worse, not faster. Single owner, no committee:

| Artifact | Why one owner |
|---|---|
| **AST schema** | Committee-designed schemas rot. One owner, RFCs from others |
| **Cache key + stage contract** | Subtle correctness; a second opinion is fine, a second author is not |
| **The three design emitters** (CSS / IDML / Typst) | Written by different people they drift, and the cross-emitter agreement gate becomes a permanent tax instead of a one-time check |
| **Preflight rules + vendor profiles** | One person who has actually read the vendor guides end to end. Split ownership produces plausible-looking rules that don't match reality |
| **The typography fixpoint** | Monotonicity and termination are easy to break and hard to notice |
| **Error taxonomy + retry policy** | Fragments instantly under multiple authors |

---

## 7. Solo / small-team reordering

With 1–2 engineers, parallelization is unavailable. Optimize for **risk retirement order** instead — do the things most likely to kill the project first, cheaply:

1. **W1–2: Can you make a PDF a printer accepts?** Skip everything else. Hand-typeset one book in whatever tool, run it through Ghostscript → PDF/X-1a → submit to IngramSpark. If that loop doesn't close, nothing else matters.
2. **W3: Order a physical proof.** Look at it.
3. **W4–6: Can the output pass for professional?** One template, hand-tuned, one real manuscript. Show it to a typographer. If the answer is no, the product thesis is wrong and you've spent 6 weeks, not 6 months.
4. **W7–10: Can you extract structure from a messy real manuscript?** The other existential risk.
5. **Only then** build the platform (CAS, cache, sandbox, orchestration) around a thing you know works.

This inverts BUILD_PLAN's order deliberately. The plan builds foundations first because that's correct engineering; solo, you cannot afford to discover in month 5 that the quality ceiling is too low.

---

## 8. Coordination protocol

Fixture-driven parallelism fails without these. All four are cheap.

**Contract window.** Schema changes land Mondays only, batched, with a version bump and a migration note. Breaking changes need an RFC. Outside the window, additive-only. Without this, five tracks spend their week chasing a schema someone edited on Thursday.

**Fixture freeze.** Fixtures are versioned artifacts in the corpus repo. Downstream tracks pin a version and upgrade deliberately. A track that regenerates fixtures silently breaks four other people.

**Contract tests are the merge gate.** A track cannot merge if its output stops validating against the published schema, or if a cross-emitter agreement test fails. This is what makes "never talk to your neighbour" safe.

**Weekly integration build, every Friday, all tracks, from W4.** Red integration stops feature work Monday. Big-bang integration at W10 after six weeks of divergence is how a 16-week plan becomes 24.

---

## 9. Parallelization risks

| Risk | Guard |
|---|---|
| Tracks build against contracts that turn out wrong | **Tracer bullet before fan-out** (§2, E3). Non-negotiable |
| Integration debt accumulates silently | Weekly integration build from W4; red stops feature work |
| Schema churn stalls everyone | Contract window + single owner + additive-only outside the window |
| Quality/compliance failures discovered late | Pull-left (§4) — vendor and proof loops start W3–W4 |
| Coordination overhead exceeds the gain | Track count ≤ team size. Don't invent a track to have one |
| Fixtures diverge from reality | Fixtures derive from the golden corpus, refreshed at each integration build |
| Agent track starts before the deterministic layer is right | Hard barrier at W11 (`pagescan` done) and W15 (A/B passed) |

---

## 10. The four levers, ranked

1. **Move prepress off the critical path.** It needs a PDF, not *your* PDF. Fixture PDFs from week 1. Worth ~3 weeks on its own.
2. **Pull every external-latency loop left** — POD proof W3, vendor submission W4, typographer W1, pentest booked W6. Worth ~4 weeks of tail, and it de-risks the two gates most likely to fail.
3. **Schema-first + fixture-driven tracks, after a tracer bullet.** The general mechanism; enables everything else.
4. **Templates as a parallel design track from W1.** Removes the typographer from the critical path entirely.

Everything else — more engineers, finer task splitting, overlapping phases — is second-order and hits coordination overhead fast.
