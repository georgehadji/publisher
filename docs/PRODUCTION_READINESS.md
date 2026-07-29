# Publisher — Production Readiness

What separates "the demo makes a PDF" from "a business runs on this."

Companion to [RESEARCH.md](RESEARCH.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [BUILD_PLAN.md](BUILD_PLAN.md).

---

## 0. Why this bar is higher than normal SaaS

Two properties of this domain change everything:

**1. Correctness is adjudicated by a third party, weeks later, at cost.**
Normal SaaS: a bug shows up in your logs. Here: your "green" PDF goes to IngramSpark, passes their gate, prints 500 copies, and the author discovers the rivers and the 4-colour black text when the box arrives. Failure is delayed, external, physical, and expensive. You cannot A/B your way out of it.

**2. The input is irreplaceable.**
A manuscript is unpublished creative work. Some customers have no clean backup. Losing one, leaking one, or *mutating one* is not a P1 — it's an existential trust event. Data-loss tolerance is zero, not "four nines."

Consequence: readiness is dominated by **output correctness infrastructure** and **data custody**, not by uptime. Uptime is the easy part.

---

## 1. Readiness levels

Ship through these; don't skip.

| Level | Meaning | Audience | Exit criteria |
|---|---|---|---|
| **L0 Demo** | makes a PDF | you | one novel → one template → looks fine |
| **L1 Alpha** | correct on known inputs | 5 friendly authors, hand-held | §2 typographic bar met on the golden corpus; preflight gate live; manual vendor submission per title |
| **L2 Beta** | correct on unknown inputs | 50 paying users, public signup, "beta" label | §2 + §3 + §4 green; 20 real vendor submissions, 0 rejections; support loop exists |
| **L3 GA** | a business runs on it | anyone, SLA, refunds | every section here signed off |
| **L4 Enterprise** | publishers & service bureaus | orgs, API, procurement | SOC 2 Type II, DPA, SSO, uptime SLA, dedicated capacity |

**GA is not a date. It's the checklist in §11.**

---

## 2. Output correctness — the actual product bar

This is where 90 % of "book formatting tools" fail, and it's the only section that determines whether you have a business. An engineer's definition of "the PDF is fine" is not a typographer's.

### 2.1 Typographic bar (per template, per trim size, gated)

**Structure**
- [ ] Front matter order correct and complete: half-title · (blank) · title · copyright (verso) · dedication · epigraph · TOC · lists · foreword/preface/acknowledgments
- [ ] Front matter folios roman, body restarts arabic at 1 on the first body recto
- [ ] Chapter openers on recto (or a stated, consistent house policy), blanks inserted automatically
- [ ] Running heads: verso/recto policy applied; **suppressed** on chapter openers, part openers, and blank versos
- [ ] Folios suppressed on blanks and (usually) drop-folio or none on chapter openers
- [ ] Back matter order: epilogue · afterword · appendices · notes · glossary · bibliography · index · about the author · also-by · colophon
- [ ] Total page count even, and signature-friendly (multiple of 4) where the binding requires it

**Composition**
- [ ] Zero widows, orphans, and runts (single-word last lines) — automated scan, gate at 0 for critical, ≤ 2 flagged for review
- [ ] ≤ 2 consecutive hyphenated line-endings; no hyphen on the last line of a page or a spread
- [ ] No rivers above threshold (rasterized column-density scan)
- [ ] Word spacing within H&J limits; no visibly loose or tight lines
- [ ] Baseline grid aligned across facing pages (body text; displaced elements documented)
- [ ] Chapter last page has ≥ 3 lines
- [ ] Language-aware hyphenation with an exception dictionary; proper nouns protected

**Detail typography** — this is what makes output read as *made by a professional*
- [ ] Real small caps (not synthesized), correct optical sizes where the family provides them
- [ ] Oldstyle figures in running text, lining in tables/folios (or a stated consistent choice)
- [ ] Correct dashes: em for parenthetical, en for ranges; no `--`
- [ ] Curly quotes and apostrophes everywhere, prime marks for measurements
- [ ] True ellipsis with correct spacing; no `...`
- [ ] Ligatures on; discretionary ligatures off unless the design calls for them
- [ ] Kerning applied; tracking used only where the optimizer justified it and **logged why**
- [ ] Optical margin alignment / hanging punctuation where the design specifies
- [ ] Drop caps sit on the correct baseline with correct optical spacing to the following text
- [ ] Scene breaks/dinkus never fall at a page break invisibly (insert visible marker or force)

**Content fidelity**
- [ ] Footnotes/endnotes split correctly across pages, numbering continuous or per-chapter as specified
- [ ] Index page references generated **after** final pagination and after parity padding
- [ ] TOC page numbers match reality (regenerate post-pagination — a classic silent failure)
- [ ] Cross-references and "see page N" resolve
- [ ] Tables don't break badly; continued-headers on splits
- [ ] Images at correct effective ppi, never upsampled, captions bound to figures

### 2.2 Prepress bar (per vendor profile, hard gate)

- [ ] Valid PDF/X-1a (or X-4 where the vendor accepts) with a correct **OutputIntent**
- [ ] All fonts embedded and subsetted, embedding permission bits verified
- [ ] No RGB / Lab / ICCBased-RGB objects when the profile demands CMYK
- [ ] **Black body text is 0/0/0/100** — never 4-colour rich black
- [ ] Total ink coverage within the vendor's limit (240–300 %)
- [ ] All rasters ≥ 300 ppi at placed size (≥ 600 for 1-bit line art)
- [ ] MediaBox/TrimBox/BleedBox present and correct; bleed area actually contains art, not white
- [ ] No transparency (X-1a), no unmanaged spot colours
- [ ] No annotations, forms, JavaScript, embedded files, or layers
- [ ] Margins satisfy the vendor's gutter curve for the final page count
- [ ] Spine width matches final page count **and** the chosen paper stock; cover built after the interior converged

### 2.3 The correctness infrastructure that makes the bar enforceable

A checklist nobody runs is decoration. These are the mechanisms:

| Mechanism | What it catches | Gate |
|---|---|---|
| **Golden corpus** — 50+ real, license-cleared manuscripts spanning genres, lengths, messiness | regressions in extraction and inference | every PR |
| **Page-raster diffing** (SSIM ≥ 0.995) per template × profile | any layout change, intended or not | every PR |
| **Text-integrity assertion** | prose mutation — the unforgivable bug | every build, always |
| **Reproducibility job** (rebuild from manifest, assert byte equality) | toolchain drift | nightly |
| **Cross-emitter agreement** (CSS/IDML/Typst) | a renderer growing its own defaults | every PR |
| **Real vendor submissions** — actually upload to IngramSpark and KDP | preflight false negatives | every release |
| **Typographer review** of every new template | the things no scan catches | template merge |
| **Print-proof loop** — order a physical POD copy of the reference book | ink, paper, gutter, spine reality | every quarter and before GA |

The last two are non-negotiable and are the ones engineering teams skip.

### 2.4 The organizational gap nobody plans for

**You need a real book designer/typographer on the team or on retainer.** Not as a nice-to-have — as a gate owner. Software engineers ship engineer-looking books: uniform, technically valid, and obviously automated. Every template merges only with a typographer's sign-off, and the blind A/B against a $900 human typesetter (BUILD_PLAN P3 gate) is the objective check that the retainer is working.

Budget it: ~$8–15k for 8 well-made templates plus an ongoing review retainer. It is the highest-ROI spend in the project.

---

## 3. Reliability

Uptime matters less than **build success rate** and **never losing work**.

- [ ] SLOs defined and instrumented: build success rate ≥ 99 %, p95 build latency, cache hit ratio, **preflight false negatives = 0** (alert on any non-zero)
- [ ] Error budget policy: burn > 50 % in a window ⇒ feature freeze until recovered
- [ ] Graceful degradation paths: LLM down ⇒ rules-only inference with an honest banner; Adobe path down ⇒ fall back to Path A and say so; Chrome pool saturated ⇒ queue with a position, never a silent drop
- [ ] Long-running work survives deploys (durable workflow state; human gates hold for days)
- [ ] Idempotency on every mutating endpoint; retries never double-charge or double-build
- [ ] Backpressure end-to-end; no unbounded accept
- [ ] **Backups restored, not just taken** — quarterly restore drill, timed, documented, with a named owner
- [ ] RTO/RPO stated and proven: RPO ≤ 5 min for metadata, **0 for uploaded manuscripts** (write to durable storage before ACK)
- [ ] DR runbook for region loss; artifacts replicated cross-region
- [ ] On-call rotation, paging, escalation, and a runbook per alert (an alert without a runbook is a bug)
- [ ] Load tested at 10× expected peak; soak tested 1000 books with flat RSS
- [ ] Chaos: kill a worker mid-build, kill the DB primary, fill the disk, saturate the pool — each has a known, tested outcome

---

## 4. Security

Threat model: hostile file uploads, multi-tenant leakage, and the theft of unpublished manuscripts (which have real black-market and pre-publication-leak value).

- [ ] Written threat model per module; STRIDE pass on ingest, render, and API
- [ ] Sandbox controls verified by test, not by config review: no egress (eBPF-audited), read-only rootfs, seccomp, non-root, dropped caps, cgroup limits, wall-clock kill
- [ ] Malicious-input corpus in CI: zip bombs, billion-laughs, XXE, path traversal, malformed OOXML, font fuzz, decompression bombs. DTD/entity settings **asserted in unit tests** (a library upgrade can silently re-enable them)
- [ ] Ghostscript, LibreOffice, Chrome pinned by digest, sandboxed at the strictest tier, CVE-gated in CI
- [ ] Tenant isolation: app-level checks **plus** Postgres row-level security; per-tenant KMS keys; presigned URLs short-TTL and single-artifact
- [ ] Authz matrix test: every endpoint × every role × own-tenant/other-tenant
- [ ] Secrets in a manager with rotation; no secrets in env files in the repo; leak scanning on every push
- [ ] Supply chain: SBOM per image, signed images (cosign), admission policy rejects unsigned, dependency review on every PR, engine upgrades treated as release events
- [ ] **Third-party penetration test before GA**, findings remediated, retest passed
- [ ] Vulnerability disclosure policy + `security.txt` + a monitored inbox
- [ ] Incident response plan with severity levels, comms templates, and a named incident commander; rehearsed once
- [ ] Audit log: append-only, tamper-evident, covers auth, manuscript access, override changes, and artifact downloads

---

## 5. Data protection & privacy

- [ ] Data map: what's stored, where, for how long, who can reach it
- [ ] Encryption at rest (per-tenant keys) and in transit; artifacts never publicly readable
- [ ] Retention policy published and enforced: intermediates GC'd, delivered artifacts retained (customers reprint years later), sources per contract
- [ ] **Deletion actually deletes** — CAS, cache index, read models, logs, and backups, within the stated window, with a verification job
- [ ] Export: a customer can take their manuscript, AST, design spec, and all artifacts and leave
- [ ] GDPR/UK-GDPR: lawful basis, privacy policy, DSAR process with an owner and an SLA, records of processing
- [ ] **Subprocessor list published** — and the LLM provider is on it. Customers will ask "is my novel being used to train a model." Have the contractual answer in writing, and prefer a zero-retention/no-training API tier.
- [ ] DPA available for business customers; SCCs where data crosses borders
- [ ] Internal access controls: engineers cannot read customer manuscripts by default; break-glass access is logged, time-boxed, and notified
- [ ] Staging/dev never contains real customer manuscripts — synthetic or explicitly licensed corpus only

---

## 6. Legal & compliance

The section that sinks publishing products.

- [ ] **Font licensing enforced in code** (domain layer, not UI): every shipped face has a license covering *server-side generation* + *PDF embedding* + *ebook embedding*. OFL-only default set; premium faces licensed properly; tenant uploads require an attestation and are hash-tracked.
- [ ] License provenance recorded per build — you can prove, per delivered PDF, which fonts were embedded under which license
- [ ] Adobe: if the InDesign API path ships, terms reviewed, entitlement in place, and usage within the agreement
- [ ] ICC profiles: redistribution rights confirmed (FOGRA39 via ECI, GRACoL/SWOP terms)
- [ ] Templates: any bundled ornaments, rules, or artwork are original or properly licensed for redistribution
- [ ] ToS + AUP: content ownership stays with the author (say it plainly and prominently), acceptable use, no infringing content, termination terms
- [ ] **Limitation of liability + a stated print guarantee.** Turn the scary risk into a bounded, priced one: *"If a build we marked preflight-green is rejected by a supported vendor, we fix it free and refund the print run up to $X."* This is simultaneously risk control and a sales asset — nobody else offers it.
- [ ] E&O / professional liability insurance sized to that guarantee
- [ ] Copyright/DMCA process: notice-and-takedown, repeat-infringer policy, agent registered
- [ ] Accessibility of **your own web app**: WCAG 2.1 AA (you're selling accessibility compliance; failing it yourself is indefensible)
- [ ] EAA: EPUB output validated by EPUBCheck **and** ACE by DAISY; conformance statement generated from the ACE report; customer-facing guidance on their own obligations
- [ ] ISBN/ONIX correctness — bad metadata propagates through the retail supply chain and is painful to unwind
- [ ] Export/sanctions screening if selling internationally; tax/VAT/GST handling for digital services

---

## 7. Operations

- [ ] Support channel with a stated response SLA; a triage rota; a path from ticket → repro bundle → issue
- [ ] **Repro bundle in one click**: any build reproducible from its manifest by support without engineering. This is the single highest-leverage support investment.
- [ ] Status page + incident comms templates; proactive notification, not customer-discovered outages
- [ ] Runbooks: stuck build · Chrome pool wedged · vendor spec changed · engine upgrade rollback · restore from backup · suspected data leak
- [ ] Release process: canary → progressive rollout → automated rollback trigger; feature flags for anything risky
- [ ] **Engine upgrades are a deliberate release event** — Chrome/Ghostscript/LibreOffice/Saxon bumps invalidate the cache and change layout. Full golden-corpus + raster-diff run required to merge. Never let dependabot merge these.
- [ ] Vendor-spec watch: KDP/IngramSpark change requirements. Versioned profile data, a periodic check, and a real submission each release.
- [ ] Cost observability per build and per tenant; spend ceilings on LLM and Adobe with graceful degradation
- [ ] Abuse controls: per-tenant quotas, upload size/rate limits, and a check that nobody is using you as a free PDF farm
- [ ] Capacity plan with lead time for the Chrome pool (the expensive, slow-to-scale tier)

---

## 8. Product readiness

- [ ] **The escape hatch exists.** Automation will fail on some manuscripts. Ship one of: an IDML handoff ("open it in InDesign"), a human-assist tier, or a partner designer network. A tool with no escape hatch generates unhappy customers you cannot help.
- [ ] Onboarding that sets expectations honestly: what this does well, what it doesn't, what a "messy" manuscript costs in review time
- [ ] The preflight report is **customer-readable** — plain language, per-finding remedy, jump-to-page. Not a JSON dump.
- [ ] Proof workflow: watermarked proof PDF + page previews before the final build; encourage a physical POD proof and say so in the flow
- [ ] Revision loop is first-class, not an afterthought — authors *always* send a v2
- [ ] Template catalogue with honest previews rendered from real text, not lorem ipsum
- [ ] Docs: getting started, manuscript-prep guide (huge deflection value), vendor-specific guides, API reference, changelog
- [ ] Pricing, billing, entitlements, quotas, dunning, invoices, refunds — all wired, all tested
- [ ] Analytics on the funnel: upload → structure-review completion → first build → delivered. The structure-review step is where you'll lose people; instrument it hardest.

---

## 9. Engineering hygiene (table stakes, listed so it's not skipped)

- [ ] ≥ 80 % coverage overall; ~100 % on prepress rules, geometry math, and the integrity gate
- [ ] Property-based tests on invariants, not only examples
- [ ] CI: lint, types, unit, integration, corpus, raster diff, security scan, SBOM, license check — all blocking
- [ ] Zero flaky tests tolerated; quarantine with an owner and a deadline
- [ ] ADRs for every significant decision (they exist so the next engineer doesn't relitigate)
- [ ] Every module README carries its threat model and memory budget
- [ ] Structured logging with trace correlation; no PII or manuscript text in logs — **ever** (assert it in a test)
- [ ] Dashboards for the SLOs; alerts all have runbooks
- [ ] Infrastructure as code; environments reproducible from scratch; a documented, timed "rebuild prod from zero" drill

---

## 10. What production-ready does **not** require

Scope discipline is part of readiness. These are explicitly out for GA:

- Non-Latin scripts (Arabic, Hebrew, CJK, Indic) — deliberate v2 project, stated plainly in marketing
- Every trim size — ship 6–8 real ones, well-made
- Every vendor — KDP + IngramSpark covers the overwhelming majority
- Heavily designed books: cookbooks, children's picture books, coffee-table, complex textbooks — say no clearly rather than badly
- Real-time collaborative editing
- Multi-region active-active
- Full InDesign parity

Saying "we don't do that yet" is professional. Doing it badly is not.

---

## 11. GA sign-off sheet

Ship when every line has a name and a date against it.

**Output**
- [ ] Typographic bar (§2.1) verified per template × trim size, signed by the typographer
- [ ] Prepress bar (§2.2) enforced as a hard gate, no override path exists in the API
- [ ] Golden corpus green; raster diffs green; cross-emitter agreement green
- [ ] 20 real vendor submissions, **0 rejections on green preflight**
- [ ] Physical POD proof ordered, received, and reviewed for the reference book
- [ ] Blind A/B vs a professional typesetter: ≥ 50 % prefer or cannot distinguish

**Trust**
- [ ] Text-integrity gate live, 0 violations across all history
- [ ] Nightly reproducibility job green for 30 consecutive days
- [ ] Backup restore drill completed and timed
- [ ] Third-party pentest passed and remediated
- [ ] Data deletion verified end-to-end including backups

**Business**
- [ ] Font licensing enforced in code and provable per build
- [ ] ToS, privacy policy, DPA, subprocessor list, AUP published
- [ ] Print guarantee stated and insured
- [ ] Support rota, runbooks, status page, incident plan live
- [ ] Billing, quotas, and refunds tested end-to-end
- [ ] Escape hatch (IDML handoff or human-assist tier) shipped

**Operations**
- [ ] SLOs instrumented with alerts and runbooks
- [ ] Load and soak tests passed at 10× peak
- [ ] Canary + rollback proven in production
- [ ] Engine-upgrade process documented and exercised once

---

## 12. The three that actually decide it

Everything above is necessary. These three are what determine whether the product is *professional*:

1. **A typographer gates the templates.** Without that, the output is technically valid and visibly automated, and the market can tell in three seconds.
2. **Preflight-green means the printer accepts it — proven by real submissions, backed by a refund guarantee.** That claim is the product.
3. **The author's words are never altered, and the manuscript is never lost or leaked.** Everything else is recoverable. These are not.
