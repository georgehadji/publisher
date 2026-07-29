# Publisher — Research: DOCX → Print-Ready Book PDF (and beyond)

Research date: 2026-07-29. Target: SaaS that ingests `.doc`/`.docx` and emits a **professional, print-ready PDF** (plus InDesign artifacts, EPUB, metadata) with no human typesetter in the loop.

---

## 0. Verdict first

**Do not build "DOCX → InDesign".** Build **DOCX → canonical semantic book model → N renderers**. InDesign becomes one renderer among several, not the spine of the product.

Recommended architecture:

```
.doc/.docx
   ↓  [ingest]        LibreOffice headless (doc→docx) → XSweet/Mammoth/docx4j
   ↓  [normalize]     OOXML → HTML "typescript" (appearance-faithful, no semantics)
   ↓  [infer]         rules + LLM classifier → BOOK AST (JSON, versioned, hash-verified)
   ↓  [review]        web UI: chapter map, style overrides, front/back matter
   ↓  [compose]       ┌ Renderer A: HTML+CSS Paged Media → PDF     (default, 95%)
                      ├ Renderer B: IDML/ICML → InDesign → PDF/X   (premium/handoff)
                      ├ Renderer C: Typst/LaTeX → PDF              (STEM/academic)
                      └ Renderer D: EPUB 3 + a11y                  (digital)
   ↓  [finish]        Ghostscript → CMYK + PDF/X-1a/X-4, bleed, marks, spine
   ↓  [preflight]     rule engine + veraPDF/pdfcpu + vendor profile gate
   ↓  [deliver]       KDP / IngramSpark / Lulu-ready PDF + INDD/IDML + EPUB + ONIX
```

Key insight: **the canonical AST is the product**. Everything else is a swappable backend. Ship Renderer A first; add B when a customer pays for designer handoff.

---

## 1. Market — is this worth building

| Signal | Number | Source |
|---|---|---|
| Global self-publishing market (2025) | **$2.16B**, +16.7%/yr | automateed / worldmetrics |
| Self-pub ISBN titles issued 2025 | **3.5M+**, +38.7% YoY | selfpublishing.com |
| KDP titles/year | **~1.4M** | WordsRated |
| Traditional publishing growth | ~1%/yr | same |
| Human typesetting cost | **$2.00–3.50/page** → ~$600–1,100 per 300pp book | Gorham, Mayfly, Star Print Brokers |
| Vellum | $249.99 ebook / $299.99 + print — **Mac only** | Kindlepreneur |
| Atticus | $147, cross-platform | creativindie |
| Reedsy Studio | free, browser | — |

Gap: Vellum/Atticus are **template pickers**, not typesetting engines. They produce "acceptable indie" output, not "trade-house" output. Nobody sells **API-first, PDF/X-compliant, designer-handoff-capable** automation at indie prices. Bookalope is closest and is a decade-old niche tool.

Wedge: **quality ceiling + handoff artifact**. "Output that passes IngramSpark preflight first time, and an .indd your designer can open."

---

## 2. Stage 1 — Ingest

### 2.1 `.doc` (binary, pre-2007)
No good native lib. Only sane path: **LibreOffice headless** `soffice --headless --convert-to docx`. Containerize; it's slow (2–10 s), memory-hungry, and occasionally hangs — run it in a sandboxed worker with hard timeout + OOM kill. Pandoc **cannot** read `.doc`.

### 2.2 `.docx` (OOXML)
Three tiers, pick by fidelity need:

| Tool | Lang | Output | Strength | Weakness |
|---|---|---|---|---|
| **XSweet** (Coko) | XSLT 2.0 | HTML "typescript" | Best-in-class; purpose-built for book production; battle-tested in Ketty/Editoria; separates *extraction* from *interpretation* (HTMLevator) | XSLT toolchain (Saxon), steep |
| **Mammoth** | py/js/java | semantic HTML | Clean, `style_map` for custom Word styles → tags | Deliberately drops direct formatting; "works best if you only use styles" — most manuscripts don't |
| **python-docx / docx4j** | py / java | raw OOXML objects | Full control, footnotes/endnotes/comments/fields/index entries | You write everything |
| **Pandoc** | Haskell | AST → anything | Free ICML/EPUB/LaTeX writers, huge format matrix | Lossy on direct formatting; own AST is not book-shaped |

**Recommendation:** XSweet (or docx4j) for extraction → your own AST builder. Use Pandoc as a *fallback/secondary* writer, not the core.

### 2.3 What manuscripts actually contain (plan for all of it)
Tracked changes • comments • footnotes AND endnotes • embedded/linked images (EMF/WMF!) • equations (OMML) • tables • text boxes • fields (TOC, page refs, cross-refs) • index entries (`XE` fields) • bookmarks • hyperlinks • lists (numbered/bulleted, restart semantics) • section breaks • headers/footers • drop caps • language runs • RTL/CJK • smart quotes vs straight • manual line breaks used as paragraph breaks • **hard-coded formatting instead of styles (the norm)**.

EMF/WMF → must rasterize or convert to SVG (LibreOffice or `libwmf`/Inkscape); most PDF engines can't consume them.

---

## 3. Stage 2 — The canonical Book AST (the real product)

Design decisions:

- **Format**: JSON, versioned schema, content-addressed. Not XML (dev ergonomics), but keep a JATS/BITS export for scholarly customers.
- **Node types**: `book > part > chapter > section > {para, heading, blockquote, verse, list, table, figure, footnote, epigraph, dinkus/scenebreak, dialogue, sidebar, code, equation, pagebreak, toc-marker, index-entry}`.
- **Front matter / body / back matter** explicitly modeled: half-title, title, copyright, dedication, epigraph, TOC, foreword, preface, acknowledgments, prologue … epilogue, afterword, appendix, notes, bibliography, index, about-the-author, also-by, colophon.
- **Style intent, not style values**: store `role: "chapter-opening-paragraph"`, not `first-line-indent: 0`.
- **Provenance**: every node keeps `sourceRef` (docx paragraph id) so the review UI can jump back and reingest deltas.

### 3.1 Text-integrity invariant (non-negotiable)
```
normalize(concat(text nodes of AST)) == normalize(text stream of source docx)
```
Assert on every build. Author prose must be **byte-preserved** through the pipeline. This is the single most important correctness guard and the one an LLM stage will silently violate.

### 3.2 Structure inference — rules first, LLM second
Deterministic signals: Word style names (`Heading 1`, `Chapter Title`), outline level, font size/weight relative to body median, centering, all-caps, `\f`/section breaks, page-break-before, numbering ("Chapter 7", "VII", "Seven"), short paragraph followed by break, repeated ornament glyphs (`* * *`, `###`, `~`).

LLM layer — **classification only, never generation**:
- Which paragraph is a chapter title vs a heading vs a false positive
- Front/back matter classification
- Verse vs prose vs epigraph vs dialogue-heavy
- Genre/trim-size suggestion, running-head text (short title)
- Ambiguous italic runs: emphasis vs title-of-work vs foreign term (affects EPUB semantics + `lang`)

Constrain with structured output (enum labels + node ids). Reject any response that emits text. Confidence score → below threshold routes to human review UI.

---

## 4. Stage 3 — Composition. The four real paths.

### Path A — HTML + CSS Paged Media (recommended default)

| Engine | License | Notes |
|---|---|---|
| **Paged.js** | MIT (Coko) | Polyfill; runs in Chrome. Footnotes, running heads from content, cross-ref page numbers. Used by Ketty, Nvcleus. Chrome and Paged.js disagree on parts of the spec — pin versions. |
| **Vivliostyle** | AGPL/commercial | CLI; browser-based; mature CSS Paged Media impl |
| **PrinceXML** | Commercial — **~$3,800/yr server**, $495 desktop, ~$2,000/yr startup tier; free w/ watermark | Best-in-class output quality, PDF/X capable, book-publisher edition in development. DocRaptor resells as API. |
| **PDFreactor / Antenna House** | Commercial, enterprise $$ | AH is the gold standard for CJK/complex scripts |
| **WeasyPrint** | BSD, Python | Free; weakest pagination features of the set |

Gotchas with headless Chrome (Paged.js path):
- **Headless `--print-to-pdf` ≠ GUI "Print to PDF"** — different rendering paths. Drive it via **CDP `Page.printToPDF`**, not the CLI flag.
- Chrome **silently drops `url()` resources referenced from `@page` rules** → inline as data URIs.
- Chrome emits **RGB only**. No CMYK, no spot colors, no PDF/X. → mandatory Ghostscript post-process (§5).
- Long books: default CDP timeouts blow up; raise them and stream.

Verdict: Paged.js for v1 (free, open, controllable). Prince as a paid "quality tier" if output diffing justifies the $3.8k.

### Path B — InDesign (IDML/ICML). The "professional" path.

**IDML** = ZIP of XML (stories, spreads, master spreads, styles, resources). **Fully generatable without InDesign installed.** ICML = single-story InCopy content, `File > Place` into an existing template.

Libraries ([awesome-idml](https://github.com/paged-media/awesome-idml)):

| Lang | Lib | Use |
|---|---|---|
| Python | **SimpleIDML** | Compose/split/merge IDML packages; production at Le Figaro; optional InDesign Server export |
| Python | outdesign, idml2html, idml2docbook | round-trip |
| PHP | IDMLlib, idml-json-converter, markdown-idml-converter | |
| JS/TS | **idmlkit** (AI-native, layout-preserving), imgly/idml-importer, DeepIDML | |
| Rust | idml-to-pdf (libHaru) | direct PDF, limited |
| Java | Apache Tika IDMLParser | text/metadata only |
| XSLT | transpect/idml2xml | scholarly pipelines |
| **Pandoc** | native `icml` writer | `pandoc in.docx -s -o out.icml` — fastest route to a placeable story |

Pandoc ICML writer known limits (all confirmed open issues): footnote **inner styles (bold/italic) are lost**; inline-footnote regressions; tables lack rowspan/colspan/head-foot/multi-header/block captions; images only single-image figures, frame sizing must be explicit overrides rather than graphic-frame styles.

→ For anything past a plain novel, **generate IDML yourself** from the AST rather than leaning on Pandoc ICML.

**Who can open IDML** (this is the strategic value — you're not locked to Adobe): InDesign (r/w), VivaDesigner (near loss-free r/w), Affinity Publisher 1.8+ (read), QuarkXPress (read), Scribus 1.6+ (read).

**Actually rendering INDD → PDF/X** needs an InDesign engine. Three options:

| Option | Cost | Reality |
|---|---|---|
| **Adobe InDesign APIs** (Firefly Services) | Enterprise agreement; Firefly Services ~$1k/mo minimum commit territory; credit-based | Cloud, no infra. Endpoints: **Rendition** (PNG/JPG/**PDF**), **Custom Scripts** (run arbitrary ExtendScript/UXP → this is the escape hatch: place ICML, apply styles, export PDF/X preset), **Data Merge**, **Document Info**, **Convert PDF→InDesign** (Feb 2026), **Remap Links**. OAuth 2.0 server-to-server, 24h tokens. Assets via presigned URLs (S3 / Azure / Dropbox / GCS). **2 GB max asset**. Async job + Status API. |
| **InDesign Server** self-hosted (via Datalogics) | **from ~$2,100/yr** license; realistic in-house infra build quoted at **$50k+** | Windows/macOS VMs, SOAP/Java API, ExtendScript ports 1:1 from desktop. You own orchestration, licensing, font install, crash recovery. |
| **Managed InDesign Server API** (3rd party, e.g. MetaDesign) | per-page pricing, license included | Fastest path to "real InDesign" without procurement |

Scripting: **ExtendScript** still wins for headless/batch and is what InDesign Server runs; **UXP** is the go-forward for panels and has thinner DOM coverage. Write batch logic in ExtendScript; it ports desktop → Server → Adobe Custom Scripts API unchanged.

**Recommendation for Path B:** generate IDML from AST with your own writer (SimpleIDML or a hand-rolled Rust/TS writer). Ship IDML as a **deliverable** on day one (zero Adobe cost — customer/designer opens it). Add Adobe Custom Scripts API only when you need server-side PDF/X out of InDesign specifically.

### Path C — Typst / LaTeX / SILE

| Engine | Speed | PDF/X | CMYK | Verdict |
|---|---|---|---|---|
| **Typst** 0.14+ | ms — orders of magnitude faster than LaTeX | ❌ **No PDF/X** (open issue #6012). PDF 1.4–2.0, PDF/A all parts, PDF/UA-1 ✅ | ❌ not native | Superb for speed + programmability; needs Ghostscript to reach print spec |
| **LuaLaTeX/pdfTeX + `pdfx.sty`** | slow | ✅ **PDF/X-1a native**, ships free `coated_FOGRA39L_argl.icc` | ✅ | The only FOSS engine with first-class PDF/X. Ugly to template, unbeatable paragraph composition (Knuth–Plass) |
| **SILE** | ~100× faster than LaTeX, ~10–100× slower than Typst | limited | limited | Frame-based layout, real footnote/RTL/TTB/hyphenation support from day one. Small ecosystem. |
| **ConTeXt** | slow | ✅ good print support | ✅ | Strong colour/imposition; tiny community |

Typography note: InDesign's **Paragraph Composer** optimizes across multiple lines (kills widows/orphans/rivers); its Single-line Composer does not. TeX's Knuth–Plass is the same class of algorithm and arguably better. Browser engines (Chrome) are **single-line greedy** → measurably worse justification. This is the strongest technical argument for Path B or C over Path A for premium tiers.

### Path D — Scribus
Free, Python scripting, real CMYK + PDF/X-1a/X-3/X-4 export, imports IDML. Headless scripting is fragile and the API is quirky, but it's the only **free** engine that natively exports PDF/X *and* reads IDML. Worth a spike as a zero-license fallback for the finishing step.

---

## 5. Stage 4 — Making it actually print-ready (where 90% of competitors fail)

### 5.1 PDF/X flavor

| | PDF/X-1a | PDF/X-4 |
|---|---|---|
| Transparency | flattened | **live** |
| Color | forced DeviceCMYK | ICC-managed, device-independent |
| Layers | no | yes |
| POD acceptance 2026 | **safest, universally accepted** | widely accepted, verify per vendor |

**IngramSpark demands PDF/X-1a, CMYK, 300 dpi, fully embedded fonts.** KDP is looser (accepts RGB interiors). → Default to **PDF/X-1a:2003**, offer X-4 as an option.

### 5.2 Ghostscript finishing (the workhorse)
```bash
gs -dPDFX -dBATCH -dNOPAUSE -dNOOUTERSAVE \
   -dPDFXCompatibilityPolicy=1 \
   -sDEVICE=pdfwrite \
   -sColorConversionStrategy=CMYK \
   -dProcessColorModel=/DeviceCMYK \
   -dOverrideICC=true \
   -sDefaultRGBProfile=sRGB.icc \
   -sOutputICCProfile=CoatedFOGRA39.icc \
   -dRenderIntent=3 \
   -dDeviceGrayToK=true \
   -dSubsetFonts=true -dEmbedAllFonts=true \
   -dDownsampleColorImages=false \
   -sOutputFile=out.pdf PDFX_def.ps in.pdf
```
- `PDFX_def.ps` carries the OutputIntent (`/OutputConditionIdentifier`, registry, ICC stream). Required for X-1a validity.
- `-dDeviceGrayToK=true` is critical: keeps black body text as **100K only**, not 4-color rich black. 4-color black text = registration fringing = printer rejection.
- Never downsample; upstream should already be ≥300 ppi (≥600 ppi for line art).
- ICC profiles: **Coated FOGRA39** (ECI, free) for Europe, **GRACoL 2013 / SWOP** for US. `pdfx.sty` bundles a freely distributable FOGRA39.

### 5.3 Geometry — computed, not guessed
Per-vendor profile config drives everything:

```yaml
kdp_paperback_6x9:
  trim: [6.0, 9.0] in
  bleed: {sides: 0.125, top: 0.125, bottom: 0.125}   # KDP: 0.125" on 3 sides, 0.25" total height
  outside_margin_min: 0.25
  gutter_by_pagecount:        # VERIFY against live KDP spec before shipping
    24-150:  0.375
    151-300: 0.5
    301-500: 0.625
    501-700: 0.75
    701-828: 0.875
  spine_per_page:
    white_50lb: 0.002252
    cream_50lb: 0.0025
  pdfx: X-1a
  color: cmyk_optional        # KDP accepts RGB interior
ingramspark_6x9:
  pdfx: X-1a                  # mandatory
  color: cmyk                 # mandatory
  min_image_dpi: 300
  fonts: fully_embedded
```

Spine width = `pages × per_page_thickness` (+ board/wrap allowance for hardcover; case laminate wants **0.625" wrap**). Page count must be **even**, often a **multiple of 4** for signature-based binding — pad with blanks and record it.

### 5.4 Preflight gate (own it, don't outsource the checks)
Open source only gets you partway: **veraPDF validates PDF/A + PDF/UA — not PDF/X.** Ghostscript's `-dPDFX` enforces at write time but doesn't report richly.

Build a rule engine over `pdfcpu` / `qpdf` / `mutool` / PyMuPDF that asserts:

- [ ] All fonts embedded (and no Type3 / no bitmap-only)
- [ ] No RGB/Lab/ICCBased-RGB objects when profile says CMYK
- [ ] Black text is `0,0,0,100` — not rich black
- [ ] Every raster ≥ 300 ppi at placed size (≥600 for 1-bit)
- [ ] TrimBox/BleedBox/MediaBox present and correct; bleed actually filled with art
- [ ] No transparency (X-1a) / no unmanaged spot colors
- [ ] Page count even + parity of recto/verso (chapter openers on recto)
- [ ] Total ink coverage ≤ 240–300% (vendor-dependent)
- [ ] OutputIntent present, matches declared condition
- [ ] No annotations/forms/JS/embedded files
- [ ] Widow/orphan/runt scan, river detection, stacked-hyphen check (≤2 consecutive), loose/tight line detection

Commercial escape hatch if you want certified verdicts: **callas pdfToolbox** (CLI/SDK, is what many printers run), Enfocus **PitStop Server**. Budget a license once you have paying customers.

### 5.5 Typographic quality checks (the differentiator)
Automate the things a compositor is paid to catch:
- widows (last line of para alone atop page), orphans (first line alone at page bottom), **runts** (single word on last line)
- ≥3 consecutive hyphenated line-ends
- rivers (vertical whitespace channels — detect via rasterized column density scan)
- last page of chapter with <3 lines
- facing-page baseline alignment (grid lock)
- running heads suppressed on chapter openers / blank versos
- widow/orphan control at 2 lines resolves the large majority automatically; the rest need composition-level fixes (tracking ±0.005 em over a paragraph, hyphenation zone tweaks) — implement as an **optimizer pass**, not manual nudges.

Render page rasters and diff them across builds → regression testing for layout.

---

## 6. Beyond PDF ("and even beyond")

| Deliverable | Why | How |
|---|---|---|
| **EPUB 3 + a11y** | **European Accessibility Act in force 28 Jun 2025.** Ebooks sold in EU (incl. backlist) need WCAG 2.1 AA + EPUB Accessibility 1.0 + accessibility metadata. Microenterprise exemption: <10 staff **or** <€2M turnover — covers most indies but **not your SaaS customers who are publishers**. | Generate from same AST. Validate with **ACE by DAISY** + EPUBCheck. Ship `schema:accessibilityFeature/Hazard/Summary` metadata. |
| **IDML / INDD** | Designer handoff — the moat. "Take it further in InDesign." | AST → IDML writer |
| **Cover** | Spine width derives from final page count — only your pipeline knows it | Template + computed spine; PDF/X-1a, CMYK, bleed |
| **ONIX 3.0 metadata** | Required by Ingram/retail distribution | AST metadata + user input |
| **Kindle (KPF/AZW)** | KDP ingest | Kindle Previewer 3 CLI, or ship EPUB (KDP converts) |
| **Audiobook script / TTS marks** | Adjacent upsell | SSML from AST |
| **Large print / accessible print** | Same AST, different profile | trivially cheap once AST exists |
| **Web/serialized HTML** | Author marketing | same |

---

## 7. Landmines (read before writing code)

1. **Font licensing is the #1 legal risk.** Desktop / web / app / **ebook-PDF** / **server** are separate license tiers. Server-side PDF generation on behalf of customers = **server tier**, which most foundries price separately and some forbid. Adobe Fonts explicitly do **not** cover a workflow where font files move between parties.
   → Ship with **OFL/Apache** faces only by default: EB Garamond, Crimson Pro, Libre Caslon, Source Serif 4, Alegreya, Vollkorn, Cardo, Spectral, Literata, Bitter, IBM Plex Serif. Buy proper server/embedding licenses for a small premium set. Let enterprise customers upload their own licensed fonts + click an attestation.

2. **Adobe entitlement gate.** InDesign APIs need an enterprise agreement + Adobe Developer Console OAuth S2S creds. Not self-serve. Plan a 4–12 week procurement if Path B is on the critical path — which is exactly why Path B must not be on the critical path for v1.

3. **Chrome ≠ print engine.** RGB-only, single-line composer, `@page url()` silently dropped, headless≠GUI. Every one of these has bitten shipped products.

4. **Garbage in.** Real manuscripts use direct formatting, not styles. Mammoth's "works best if you only use styles" is a polite way of saying it fails on the median input. Your inference layer *is* the product; budget accordingly.

5. **LLM prose contamination.** Enforce the text-integrity hash. A single "helpfully" fixed typo in a customer's novel is a trust-ending bug.

6. **Reproducibility.** Same input + same version must produce byte-identical PDF (modulo `/ID`). Pin engine versions, font versions, hyphenation dictionaries, ICC profiles. Store the full toolchain manifest in the job record. Customers reprint years later.

7. **Non-Latin scripts.** Arabic/Hebrew RTL, CJK line-breaking (kinsoku), Devanagari shaping. Chrome/HarfBuzz handles shaping; pagination rules do not. Scope out of v1 explicitly, or use SILE/AH.

8. **Print vendor drift.** KDP/Ingram change specs. Version the vendor profiles, test nightly against their published guides, and keep an "as-submitted spec version" on every job.

---

## 8. Competitive landscape

| Product | Model | In | Out | Gap you exploit |
|---|---|---|---|---|
| **Vellum** | $250–300 one-time, **Mac only** | docx | EPUB, print PDF | No Windows, no API, template-limited, no PDF/X, no IDML |
| **Atticus** | $147, cross-platform | docx | EPUB, PDF | Same class; no API/handoff |
| **Reedsy Studio** | free | web editor | EPUB, PDF | Limited customization |
| **Lacuna** | desktop app | docx, auto chapter detection | EPUB, print PDF for KDP/Apple/Ingram | Closest indie competitor; desktop-bound |
| **Bookalope** | web + **REST API** + InDesign CEP panel | docx & more | EPUB2/3, MOBI, PDF, **ICML**, DOCX | Closest architecturally. Niche, aging, weak on print-spec rigor |
| **Nvcleus** | platform | docx (XSweet) | Paged.js PDF, EPUB, **ICML** | Validates the exact stack recommended here |
| **Ketty / Coko** | **open source** (AGPL-ish) | docx (XSweet) | Paged.js PDF, EPUB | Free foundation you can build on / steal patterns from |
| **Pressbooks** | open source SaaS | web | PDF, EPUB | Academic/OER focus |
| Human typesetter | $600–1,100/book | anything | anything | 2–4 week turnaround |

**Nobody** in the indie tier ships: PDF/X-1a-certified output + preflight report + IDML handoff + API. That's the position.

---

## 9. Build plan

**P0 — Skeleton (2–3 wks)**
Job queue (Redis/Temporal) + S3 + isolated workers. `.doc`→`.docx` via LibreOffice. docx→HTML via XSweet. Naive AST. HTML+CSS → Paged.js → PDF. One trim size (6×9), one template.
*Accept:* a plain novel `.docx` becomes a 6×9 PDF with correct page count, running heads, recto chapter openers.

**P1 — Print-spec compliance (2–3 wks)**
Vendor profile system. Ghostscript CMYK + PDF/X-1a + OutputIntent. Bleed/trim/marks. Spine calc. Preflight rule engine with hard gate + human-readable report.
*Accept:* IngramSpark accepts the file on first upload. Preflight report shows zero criticals.

**P2 — Inference + review UI (3–4 wks)**
Rules + LLM classifier for structure. Text-integrity hash gate. Web review UI: chapter map, reclassify, front/back matter builder, live page preview.
*Accept:* 20 real-world messy manuscripts; ≥90% chapters detected correctly; zero prose mutations.

**P3 — Typography quality pass (3–4 wks)**
Widow/orphan/runt/river detection + optimizer. Baseline grid. Drop caps, ornaments, scene breaks. 5–8 genuinely different designed templates (literary, thriller, memoir, non-fiction, poetry, academic, children's, cookbook).
*Accept:* blind A/B vs a $900 human typesetter; ≥50% prefer or can't distinguish.

**P4 — IDML handoff (2–3 wks)**
AST → IDML writer. Ship `.idml` + linked assets + style sheet as a deliverable.
*Accept:* opens clean in InDesign, Affinity Publisher, and Scribus; styles intact; text threaded.

**P5 — EPUB 3 + accessibility (2 wks)**
Same AST → EPUB 3. EPUBCheck + ACE by DAISY green. Accessibility metadata. EAA conformance statement generator.

**P6 — Beyond**
Cover generator (spine-aware), ONIX 3.0, Kindle, large-print profile, Adobe InDesign API renderer for the premium tier, public API + webhooks.

---

## 10. Concrete stack

```
API/orchestration   TypeScript (Fastify/Nest) or Python (FastAPI)
Workflow            Temporal  (durable, retriable, long jobs, versioned pipelines)
Workers             Docker, one image per engine, hard CPU/mem/time caps, no network
Ingest              LibreOffice headless · Saxon-HE (XSweet) · docx4j / python-docx
AST                 JSON Schema + Zod/Pydantic, semver'd, content-addressed in S3
Inference           deterministic rules → Claude (structured output, classify-only)
Render A            Node + Playwright(CDP) + Paged.js  →  [Prince as paid tier]
Render B            SimpleIDML / custom IDML writer  →  [Adobe InDesign API custom scripts]
Render C            Typst (speed) / LuaLaTeX+pdfx (native PDF/X)
Finish              Ghostscript 10.x + ICC (FOGRA39 / GRACoL)
Preflight           pdfcpu + PyMuPDF rule engine (+ veraPDF for PDF/A-UA, callas later)
EPUB                own writer → EPUBCheck + ACE by DAISY
Storage             S3 + presigned URLs (also satisfies Adobe API asset requirements)
Fonts               OFL set baked into image; per-tenant licensed font vault
Observability       per-stage artifacts retained; page-raster diffing as layout regression tests
```

Pricing sketch: $39–79 per title (one-off), $29–99/mo for publishers/services, API at $0.50–2/title at volume. Marginal compute cost per book ≈ pennies (Path A) — margin is fine even before Adobe.

---

## 11. Open questions to resolve before P0

1. Path B in v1 or v2? (Recommend: IDML export yes, Adobe API no.)
2. Prince license now ($3.8k) or Paged.js only? → Run output diff on 10 books first.
3. Which vendor profile is the launch gate — IngramSpark (strictest) or KDP (largest)? → **IngramSpark**; passing it means passing everything.
4. Typst as Renderer C: worth it before it has PDF/X? (Ghostscript covers it, but no CMYK-native means no spot color / rich-black control from source.)
5. Non-Latin scripts: explicit v1 exclusion?
6. Do we host customer fonts, or require attestation + upload only?

---

## Sources

- [Adobe InDesign API Overview](https://developer.adobe.com/firefly-services/docs/indesign-apis/) · [Getting Started / auth](https://developer.adobe.com/firefly-services/docs/indesign-apis/getting-started/) · [Changelog](https://developer.adobe.com/firefly-services/docs/indesign-apis/getting-started/changelog/)
- [Datalogics — Adobe InDesign Server licensing](https://www.datalogics.com/adobe-indesign-server) · [Hidden infrastructure costs of InDesign Server](https://metadesignsolutions.com/blog/beyond-the-license-the-hidden-infrastructure-costs-of-adobe-indesign-server-explained) · [Managed InDesign Server API](https://metadesignsolutions.com/adobe-indesign-server-api/)
- [ExtendScript vs UXP status 2026](https://mapsoft.com/posts/extendscript.html) · [UXP for InDesign](https://mapsoft.com/posts/uxp-indesign-workflow.html) · [InDesign batch processing](https://mapsoft.com/posts/indesign-batch-processing.html)
- [awesome-idml (library index)](https://github.com/paged-media/awesome-idml) · [SimpleIDML](https://github.com/Starou/SimpleIDML) · [SimpleIDML on PyPI](https://pypi.org/project/simpleidml/)
- [Pandoc manual (ICML writer)](https://pandoc.org/MANUAL.html) · [Markdown to InDesign via ICML](https://networkcultures.org/digitalpublishing/2014/10/08/markdown-to-indesign-with-pandoc-via-icml/) · [ICML table limits #6615](https://github.com/jgm/pandoc/issues/6615) · [ICML image frame sizing #7263](https://github.com/jgm/pandoc/issues/7263) · [ICML footnote regression #3114](https://github.com/jgm/pandoc/issues/3114)
- [python-mammoth](https://github.com/mwilliamson/python-mammoth) · [XSweet (Coko)](https://gitlab.coko.foundation/XSweet/XSweet) · [XSweet docs](https://xsweet.org/documentation/editoria-typescript/)
- [Paged.js / CSS Paged Media orgs](https://github.com/CSS-Paged-Media) · [print-css.rocks](https://print-css.rocks/) · [CSS Paged Media vs Paged.js](https://doppio.sh/guide/css-paged-media-vs-pagedjs) · [Vivliostyle (Balisage paper)](https://www.balisage.net/Proceedings/vol15/print/Wilm01/BalisageVol15-Wilm01.html)
- [Prince licensing](https://www.princexml.com/purchase/) · [Prince license FAQ](https://www.princexml.com/purchase/license_faq/) · [DocRaptor (Prince as API)](https://docraptor.com/prince)
- [Headless Chrome print-to-pdf ≠ GUI print](https://andre.arko.net/2025/05/25/chrome-headless-print-to-pdf/) · [Headless PDF printing timeouts](https://eve.gd/2021/10/18/headless-pdf-printing-in-chrome-when-the-standard-timeout-isnt-enough/)
- [Typst 0.14 release (PDF/A, PDF/UA)](https://typst.app/blog/2025/typst-0.14/) · [Typst PDF/X issue #6012](https://github.com/typst/typst/issues/6012) · [pdfx.sty docs (PDF/X-1a, FOGRA39)](https://ctan.math.illinois.edu/macros/latex/contrib/pdfx/pdfx.pdf) · [SILE — What is SILE?](https://sile-typesetter.org/what-is-sile/) · [SILE benchmarking discussion](https://github.com/sile-typesetter/sile/discussions/1907)
- [Ghostscript Color Management](https://ghostscript.readthedocs.io/en/latest/GhostscriptColorManagement.html) · [Ghostscript → PDF/A & PDF/X guide](https://www.codegenes.net/blog/how-to-use-ghostscript-to-convert-pdf-to-pdf-a-or-pdf-x/) · [Adobe: PDF/X, PDF/A, PDF/E](https://helpx.adobe.com/vn_vi/acrobat/current/pdf-x-pdf-a-pdf.html)
- [PDF Association — technical side of PDF/X](https://pdfa.org/technical-side-and-requirements-of-pdfx/) · [PDF/X-1a vs PDF/X-4](https://blog.hybridhelix.com/pdf-x-1a-vs-pdf-x-4-whats-the-difference-and-which-should-you-use-in-2025/) · [IMG.LY print-ready PDF guide](https://img.ly/blog/what-does-print-ready-pdf-mean-understanding-pdf-x-standards-for-professional-printing/)
- [veraPDF](https://verapdf.org/) · [veraPDF CLI validation](https://docs.verapdf.org/cli/validation/) · [Open-source PDF analysis tooling](https://www.bitsgalore.org/2021/09/06/pdf-processing-and-analysis-with-open-source-tools)
- [IngramSpark File Creation Guide (PDF)](https://www.ingramspark.com/hubfs/downloads/file-creation-guide.pdf) · [IngramSpark file requirements](https://www.ingramspark.com/blog/file-requirements-for-print-books) · [IngramSpark PDF/X-1a rejection fixes](https://cambric.pub/guides/ingramspark-pdf-x1a-rejected/) · [KDP & IngramSpark cover dimensions/bleed](https://www.absolutecovers.com/blog/2025/09/book-cover-dimensions-and-bleed/)
- [InDesign Paragraph Composer](https://www.herronprinting.com/resources/the-ideas-collection/adobe-indesigns-paragraph-composer/) · [Fixing widows, orphans, runts](https://creativepro.com/fixing-widows-orphans-and-runts-in-indesign/) · [Rivers, widows, orphans](https://pagination.com/fix-typography-rivers-widows-orphans/)
- [EPUB Accessibility ↔ EAA mapping (W3C)](https://www.w3.org/TR/epub-a11y-eaa-mapping/) · [EAA checklist for ebook publishers](https://publica.la/en/blog/european-accessibility-act-checklist-ebook-store-compliant) · [EAA & indie authors (ALLi)](https://selfpublishingadvice.org/european-accessibility-act/) · [Accessible ebooks / WCAG](https://www.streetlib.com/accessible-ebooks?lang=en)
- [Font licensing FAQ (Adobe)](https://helpx.adobe.com/fonts/using/font-licensing.html) · [Font redistribution FAQ (Microsoft)](https://learn.microsoft.com/en-us/typography/fonts/font-faq) · [Font licensing explained](https://changethisfile.com/blog/font-licensing-explained)
- [Bookalope](https://bookalope.net/index.html) · [Bookalope REST API](https://github.com/bookalope/Bookalope/blob/master/API.md) · [Lacuna](https://lacuna.pub/) · [Lacuna docs](https://docs.lacuna.pub/) · [Nvcleus Books features](https://nvcleus.com/books-features/) · [Coko products (Ketty)](https://coko.foundation/products.html) · [Editoria + Paged.js](https://www.pagedmedia.org/editoria-building-a-book-in-a-browser.html)
- [Vellum alternatives & pricing](https://kindlepreneur.com/vellum-alternatives/) · [Book formatting software tested 2026](https://www.creativindie.com/is-vellum-worth-it-review-best-book-formatting-software-for-pc-or-mac/) · [Best book formatting software 2026](https://lacuna.pub/compare/best-book-formatting-software)
- [Publishing by the numbers 2026](https://selfpublishing.com/publishing-by-the-numbers/) · [Self-publishing statistics 2026](https://www.automateed.com/self-publishing-statistics) · [Amazon publishing statistics](https://wordsrated.com/amazon-publishing-statistics/)
- [Interior design pricing (Gorham)](https://gorhamprinting.com/services-book-design/text-design-pricing.html) · [Mayfly Design pricing](https://mayflydesign.com/pricing/) · [Star Print Brokers book design pricing](https://www.starprintbrokers.com/book-design-pricing/) · [Jane Friedman — hiring a book designer](https://janefriedman.com/should-you-hire-a-professional-book-designer-and-formatter-for-your-book-interior/)
