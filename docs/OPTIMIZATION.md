# Publisher — Further Optimization

Research pass for optimizations beyond what's already in [BUILD_PLAN.md](BUILD_PLAN.md) and [PARALLELIZATION.md](PARALLELIZATION.md). Ranked by value. Each entry: what, evidence, impact, cost, risk, where it lands.

Headline: **one finding is large enough to change a core engine decision**, and it improves cost, latency, *and* quality simultaneously — which is rare enough to be suspicious, so it gets a verification plan rather than a recommendation.

---

## Impact summary

| # | Optimization | Axis | Est. gain | Effort | Risk |
|---|---|---|---|---|---|
| **O1** | **Typst as default renderer, Chrome as compatibility path** | cost + latency + **quality** | 5–10× on the costliest stage; removes the line-breaking risk | 2–3 wks | Med — verify by porting 2 templates |
| **O2** | **Client-side preview via typst.ts (WASM)** | latency + cost + privacy | design loop 25 s → **<1 s**, at **zero server cost** | 1–2 wks | Low |
| **O3** | **Chapter-parallel pagination + chapter-level cache** | latency | paginate 90 s → ~max(chapter); edit ch.7 invalidates only ch.7 | 2 wks | Low–Med |
| **O4** | **Two-artifact PDF policy + size budget in preflight** | cost + UX + rejections | proof PDFs 60–80 % smaller; kills a class of vendor rejections | 3 days | Low |
| **O5** | **ARM (Graviton) + Spot for all batch stages** | cost | 40–70 % on compute | 1 wk | Low |
| **O6** | **Backlist accessibility triage as the enterprise wedge** | revenue / GTM | opens a **3.5 M-title** market with a pennies-per-title product | 2 wks | Low |
| O7 | Global design-compile + inference prefix cache | cost | marginal, already partly planned | — | — |
| O8 | Synthetic corpus expansion from fixtures | quality | more coverage, no licensing cost | 1 wk | Low |

---

## O1 — Typst as the default renderer

### The finding

Typst compiles in **milliseconds**, with content changes rendering in **under a second** and a large thesis clean-building in ~15 s where LaTeX takes ~90 s. Headless Chrome, by contrast, consumes **200–400 MB RAM per instance** and takes **2–5 s per document**. And the framing that matters: Typst "was built from the ground up for exactly one thing: turning structured data into typeset documents," where HTML-to-PDF tools "repurpose a screen rendering engine to produce paper documents."

Book-specific maturity has moved. Quarto 1.9 renders `type: book` + `format: typst` as a single multi-chapter document, bringing Typst close to LaTeX parity for book structure. Typst is past v0.13 with a stable language and tens of thousands of users.

### Why this is three wins at once

**Cost.** Chrome is the identified bottleneck and the reason for the pool, admission control, memory-aware scheduling, and the 1000-book soak. Typst at ~1/10th the memory and ~1/5th the time collapses most of that infrastructure.

**Latency.** `paginate` p95 target of 90 s for 400 pp was set by Chrome. Typst puts it in seconds.

**Quality — the one that actually matters.** The risk register carries *"Chrome line-breaking is greedy single-line"* as a High-probability quality risk, mitigated only by the fixpoint optimizer and an escape to Prince/TeX for premium tiers. **Typst does real paragraph-level composition.** Adopting it doesn't mitigate that risk — it deletes it. That single change removes the largest standing threat to the P3 blind-A/B gate.

### Why the architecture already permits it

`emit_typst()` is already one of the three DesignSpec emitters ([ARCHITECTURE §2.7](ARCHITECTURE.md)). This is not an architecture change — it's a **promotion of Typst from "Renderer C, for STEM" to the default**, and a demotion of Chrome to the compatibility path for templates whose design genuinely needs CSS.

### What doesn't change

Typst still has **no PDF/X and no native CMYK**. Ghostscript finishing remains mandatory — which it already was for Chrome output. No new work.

### Risks and the verification plan

- **Design expressiveness.** Some template ideas are easier in CSS. Mitigation: keep both emitters; a DesignSpec declares its preferred engine; the cross-emitter agreement gate already exists to keep them honest.
- **Ecosystem depth** vs the browser's. Real, and the reason for keeping Chrome.
- **Verification, in the tracer bullet (W2–3), not later:** port two templates — one plain literary, one with sidebars/figures — to both emitters. Compare page-raster SSIM, line-break agreement, wall-clock, and peak RSS. Decide on data.

**Lands in:** T4, week 2. Elevate to a P0/tracer-bullet decision because it changes the shape of P3, P5, and the infra budget.

---

## O2 — Client-side preview via typst.ts (WASM)

### The finding

`typst.ts` compiles and renders Typst **in the browser via WebAssembly**, with three modes: server-side SVG embedding, server-side vector IR + client-side render, or **full client-side compilation** driving a canvas. Individually gated features for `render_canvas`, `render_svg`, `render_dom`, `render_pdf`. Multiple independent 2026 projects ship it in production.

### Impact

The design-tweak loop is where users spend most of their time, and the plan budgets **25 s** for it. With client-side compilation it becomes **sub-second**, and it costs **zero server compute** — no queue, no worker, no Chrome pool, no cache lookup.

Three secondary wins:
- **Privacy.** A tenant with `no_external_llm` and manuscript-confidentiality concerns can preview without the text leaving the browser. Directly useful in the publisher procurement conversation.
- **Offline-capable review UI.**
- **Server load shifts** from "every tweak" to "final builds only," which is a small fraction of requests.

### Constraint that must be enforced

**Preview is not the deliverable.** The final artifact is always built server-side, from the same pinned Typst version, and is what preflight and delivery see. If the client and server Typst versions diverge, previews lie — so the client bundle's engine version is part of the toolchain digest and is asserted against the server's on load.

**Lands in:** T6 with the review UI, immediately after O1 is verified. Depends on O1.

---

## O3 — Chapter-parallel pagination and chapter-level cache granularity

### The finding

Incremental layout with dirty-marking is standard in rendering engines; the research literature adds that **immutable layout boxes** enable "incremental partial update, parallel layout, progressive rendering, and backtracking layout execution," and that layout can be parallelized by re-executing **only formatters corresponding to changed portions** of the logical structure.

### Why it's sound for books specifically

A book chapter starts on a fresh page (the recto policy is already in the DesignSpec). Therefore:

- **Intra-chapter layout is independent** of every other chapter, given a starting page parity.
- The widow/orphan/river optimizer never crosses a chapter boundary.
- The only cross-chapter dependencies — folios, running heads, TOC page numbers, index locators, parity padding — are all **resolvable in a cheap stitch pass** after the chapters are laid out.

So: paginate N chapters in parallel, then stitch and renumber.

```
paginate-chapter (×N, parallel) → chapter layouts + local pagemaps
        ↓
stitch → global folios, running heads, parity padding, TOC/index locators → pagemap.json
```

### Impact

- **Latency**: `paginate` goes from serial-whole-book to `max(chapter) + stitch`. For a 30-chapter novel that's roughly an order of magnitude before O1's speedup even applies.
- **Cache granularity**: making the **chapter** the cache unit rather than the book means editing chapter 7 invalidates chapter 7's layout only. The revision loop — the actual daily workflow — becomes near-instant.

### Architecture delta

Two new registered stages (`paginate-chapter`, `stitch`) and a chapter-scoped cache key. The stage registry ([ARCHITECTURE §2.8.1](ARCHITECTURE.md)) makes adding them a declaration, not an orchestration change.

### Guard

Only valid when the design has **no cross-chapter flow** — true for books, false for magazines and some illustrated non-fiction. Gate it behind a DesignSpec capability flag, and fall back to whole-book pagination when the flag is off.

**Lands in:** T4, after O1. Defer until O1's numbers are in — Typst may make it unnecessary for the common case, though the cache-granularity win stands on its own.

---

## O4 — Two-artifact PDF policy and a size budget in preflight

### The finding

Font subsetting reduces a font's file contribution by **80–90 %** (multi-MB font → ~20 KB). Linearization ("Fast Web View") lets the first page render before the file finishes downloading. A well-optimized screen PDF is **60–80 % smaller** than its print original with no perceptible loss at screen resolution. Press quality means 300 dpi+, full font sets, print metadata, embedded profiles — none of which a customer needs to *look* at a proof.

### The change

Emit **two** PDFs, never one compromise:

| Artifact | Settings | Purpose |
|---|---|---|
| **Press** | no downsampling, all fonts embedded + subset, PDF/X-1a, OutputIntent, ICC | vendor upload |
| **Proof** | 150 dpi images, linearized, subset fonts, RGB, watermarked | what the customer views |

The proof loads fast in the browser and is cheap to store and serve. The press file is never opened by a human in a web viewer.

### The rejection class this kills

Print-on-demand vendors have **upload size limits**. A 400 pp illustrated book at 300 dpi can exceed them, and you discover it at 2 a.m. on the vendor's site rather than at build time. Add a **size budget check to the preflight rule set**, per vendor profile, so it fails as a `policy_violation` with a remedy ("reduce image count / recompress figures / split volume") like every other check.

**Lands in:** T5, P1. Three days. There is no reason not to do this.

---

## O5 — ARM (Graviton) + Spot for every batch stage

### The finding

Graviton3 is **~19 % cheaper per vCPU** than equivalent x86 on-demand and delivers **20–40 % better price-performance**; batch workers are typically CPU-bound, where the Neoverse cores do "the same work in fewer instances." Spot capacity runs **up to 90 % off**, with real-world blended savings of **59–77 %** across Kubernetes workloads. ARM + Spot compounds.

### Why this pipeline is unusually Spot-safe

Most pipelines can't use Spot for real work because interruption means lost progress. Here, **every stage is idempotent and content-addressed**: an interrupted stage loses nothing but the CPU-seconds since its last cache write, and re-running it produces the same artifact. The architecture already paid for this property; Spot is where it cashes out.

Split the fleet:

| Workload | Placement |
|---|---|
| ingest, prepress, rasterize, epub/idml writers, batch inference, nightly jobs | **ARM + Spot** |
| interactive render, API, orchestrator | on-demand (ARM where the image supports it) |
| anything with an external SLA | on-demand |

**Caveat to verify:** confirm ARM64 images and performance for Chrome, Ghostscript, LibreOffice, and Saxon before committing. Search evidence on ARM64 benchmarks for these specific tools was thin — treat as a spike, not an assumption.

**Lands in:** T1, P5 (hardening). One week, mostly image work.

---

## O6 — Backlist accessibility triage as the enterprise wedge

### The finding, with a number

There are an estimated **3.5 million backlist ebooks for sale from EU publishers**. The EAA has been in force since 28 June 2025 and applies to backlist titles sold after that date. Remediation is expensive "due to the lack of appropriate tools" and the fact that legacy titles were made before accessibility standards existed. Crucially:

> Publishers who outsource ebook production **struggle to evaluate the technical condition of their backlist titles.**

And the market expectation for 2025–26 is that publishers and service providers will be *building* remediation workflows — i.e. the tooling gap is currently open.

### The product this implies

Not "we'll remediate your backlist" (expensive to sell, expensive to deliver, crowded by incumbents). Instead:

**Backlist Accessibility Triage — cheap or free, automated, at volume.**

Ingest N EPUBs → run EPUBCheck + ACE by DAISY + your own structural checks → produce a **ranked remediation plan**: which titles are already compliant, which need metadata only, which need alt-text, which need structural rework, with an effort and cost estimate per bucket.

Why it works as a wedge:
- It solves the problem publishers **admit they cannot solve** (assessment), before selling them the expensive thing (remediation).
- It costs **pennies per title** via the Batch API — a 5,000-title backlist audit is a rounding error in compute.
- It produces a document a publisher can take to their board, which is how enterprise deals actually get funded.
- Every audited title is a qualified lead with a priced scope of work attached.

### Roadmap change

This moves from a P9 "product line" footnote to a **GTM motion that should exist by P6**, when EPUB + ACE gating is already built. The engineering is largely already scoped; what's new is packaging it as a standalone triage product rather than a step in the conversion pipeline.

---

## Second-order, worth doing but not headline

- **Global inference prefix cache with prewarming.** The classification system prefix is byte-identical across every tenant; keep it warm on the 1-hour TTL rather than re-writing it.
- **Design-compile results cached globally.** Template × profile is a small finite set; every tenant shares it.
- **CDN the page rasters.** They're immutable and content-addressed — perfect CDN objects. Cuts review-UI latency and egress.
- **Synthetic corpus expansion.** Generate messy manuscripts that mimic real Word abuse to extend coverage without licensing cost. Already listed as an internal agent; worth prioritizing because corpus licensing is on the critical path.
- **Incremental EPUB.** Same chapter-granularity argument as O3, much cheaper to implement.

---

## Explicitly rejected

| Idea | Why not |
|---|---|
| Write a custom line-breaking engine | O1 gives Knuth–Plass-class composition for free. Building one is months and a permanent maintenance tax |
| Render the final artifact client-side | Reproducibility, PDF/X, and preflight all require server-side control. Preview only |
| Multi-region active-active | Premature. Single region with tested DR is the right shape until there's a customer who requires otherwise |
| Split services finer for "scalability" | The stage registry already gives process isolation and independent deployability. Finer splitting adds coordination cost with no scaling benefit |
| Drop Chrome entirely after O1 | Keep it as the compatibility path. A single-engine bet on a young ecosystem is the wrong risk to take with a print product |

---

## Recommended sequencing

> **Integrated as of BUILD_PLAN v2.1.** These are now folded into the module catalogue and phase plan rather than tracked separately. **[BUILD_PLAN.md §5.3](BUILD_PLAN.md) is the authoritative ordering** — it carries the sequencing rules, the per-phase landing points, and the risk rows each optimization introduces. The list below is the summary it was derived from; if the two ever disagree, §5.3 wins.

1. **Now (tracer bullet, W2–3):** run the O1 verification — port two templates to both emitters, measure. This is a fork in the road and should be decided on data before five people build against one branch of it. **Promoted to a P0 gate.**
2. **P0:** O8 (synthetic corpus). **Promoted from second-order** — golden-corpus licensing is on the critical path and can slip; this is the only hedge that needs nobody else's signature.
3. **P1:** O4 (two-artifact PDF + size budget). Three days, no downside. Plus the **O5 ARM64 engine spike** — one day, and its answer can invalidate P5's fleet plan.
4. **P2:** O7 (prefix prewarming, global design-compile cache) — free inside the gateway being built.
5. **P2–P3:** O2 (client-side preview) once O1 lands. Biggest UX win in the product.
6. **P3:** O3 (chapter parallelism + chapter cache), scoped by what O1 leaves on the table. The cache-granularity half is worth doing regardless.
7. **P5:** O5 (ARM + Spot fleet split), on the spike's answer.
8. **P6:** O6 (backlist triage) as a packaged GTM motion alongside the EPUB work.

---

## Sources

- [Typst vs HTML-to-PDF: why compiled templates are the future](https://typsetter.dev/blog/typst-vs-html-to-pdf) · [What is Typst](https://typsetter.dev/blog/what-is-typst-modern-pdf-alternative-latex) · [Typst books & article layout (Posit / Quarto 1.9)](https://opensource.posit.co/blog/2026-03-31_typst-books-and-more/) · [Typst for professional book/journal production (forum)](https://forum.typst.app/t/can-typst-be-used-as-a-typesetting-engine-for-a-professional-book-or-journal-production-workflow/6121) · [Typst vs LaTeX 2026](https://www.typetex.app/comparisons/typst-vs-latex)
- [typst.ts — run Typst in JavaScript](https://github.com/Myriad-Dreamin/typst.ts) · [WebAssembly renderer docs](https://deepwiki.com/Myriad-Dreamin/typst.ts/5-webassembly-renderer) · [reflexo-typst docs](https://myriad-dreamin.github.io/typst.ts/) · [typst-wasm announcement](https://forum.typst.app/t/typst-wasm-compile-typst-in-browsers-node-and-serverless-runtimes/9399) · [privacy-first editor built on Typst WASM](https://dev.to/kakutixyz/building-a-privacy-first-resume-editor-with-typst-wasm-and-react-1d13)
- [Fast typesetting with incremental compilation](https://www.researchgate.net/publication/364622490_Fast_Typesetting_with_Incremental_Compilation) · [On the pagination of complex documents](https://www.researchgate.net/publication/221350236_On_the_Pagination_of_Complex_Documents) · [Fast and parallel webpage layout](https://www.researchgate.net/publication/221023384_Fast_and_parallel_webpage_layout)
- [PDF optimization guide](https://mapsoft.com/posts/pdf-optimization-for-web.html) · [Acrobat PDF Optimizer settings](https://helpx.adobe.com/acrobat/desktop/create-documents/optimize-pdfs/pdf-optimizer-settings.html) · [Linearize / subset fonts](https://www.lurapdf.com/optimize-pdf)
- [AWS Graviton](https://aws.amazon.com/ec2/graviton/) · [Graviton & ARM nodes: lower cost per vCPU](https://cast.ai/blog/kubernetes-arm-graviton-nodes/) · [Graviton cost savings guide 2026](https://shyam.kubeify.com/2026/02/aws-graviton-instances-40-better-price.html)
- [Accessible Backlist Ebooks Laboratory — final report](https://www.abelab.eu/outcomes/final_report/) · [A view of the European ebook backlist](https://www.abelab.eu/activities/press_release_data_19062023/) · [Is your backlist ready?](https://www.s4carlisle.com/post/the-european-accessibility-act-is-in-force-is-your-backlist-ready) · [How publishers remediate backlists in 2026](https://www.continualengine.com/blog/how-publishers-remediate-backlists-and-legacy-content/) · [EAA FAQ](https://www.kwglobal.com/blog/european-accessibility-act-faq/)
