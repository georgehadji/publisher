# Remaining work

**Status:** survey, 2026-09-23, against `23adc58`.
**Method:** every item below was checked against the code in this tree, not recalled from
a plan document. Each carries a `file:line` you can open. Where a claim is "nothing calls
X", it means a repo-wide grep excluding `node_modules/`, `.next/` and `.claude/worktrees/`
returned only comments and tests.

The repo's plan documents (`BUILD_PLAN.md`, `ARCHITECTURE_SCORE_10_PLAN.md`,
`ARCHITECTURE_UPLIFT_PLAN.md`) describe what was *intended*. This file describes what is
*absent*, which is not the same list — several planned items landed, and several things
nothing planned are missing.

---

## The shape of what's left

The pipeline's spine is real: DOCX → AST → effective doc → render → PDF/X → preflight →
package runs end to end, with two hard gates (text integrity, preflight) that cannot be
bypassed and a third (composition) added in `65f5834`.

Almost everything remaining falls into one of three shapes:

1. **Built and disconnected** — a component exists, is tested, and nothing calls it.
2. **Measured and discarded** — a value is computed and then dropped before anything reads it.
3. **Never built** — an honest absence.

Shape 2 is the dangerous one. It is the same defect class as the composition bug fixed in
`65f5834` and the agent-tool bug fixed in `23adc58`: a path that *looks* like it measures
something, produces a clean-looking result, and certifies nothing.

---

## Tier 1 — broken loops (built, tested, unreachable)

These are the highest-value items: the code already exists, so the remaining work is
wiring, not invention.

### 1.1 The human review loop does not close

The "first human gate" is three disconnected pieces.

| Piece | Where | State |
|---|---|---|
| Review UI | `packages/web/src/app/manuscripts/[id]/review/page.tsx` | **Mounted, writes ops, unauthenticated (local dev only).** A Server Component reads the structure with a server-only `PUBLISHER_API_TOKEN`; a Server Action appends retitle/flag ops. |
| Override API | `packages/api/src/routes/manuscripts.ts` | **Done.** `PATCH /v1/documents/:id/overrides` validates each op against `overrides/1`, refuses (422) ops `resolve` cannot apply, and appends to `override_ops` (migration `004`) in one transaction. The log is append-only (the app role holds SELECT and INSERT only), tenant-isolated by RLS, and ordered by `seq`. A reused op id is a 409. `GET .../structure` returns the log in order instead of `[]`. |
| Override consumer | `stages/resolve_stage.py:38` | **Done.** `resolve` takes `overrides_path` as an optional root input, and the worker supplies it (see **Worker** below). |

**Parser — done.** `resolve` used to build ops with `OverrideOp(**op)`, and the Python
dataclass does not match the schema (`from_value`/`to_value`/`created_at` for
`from`/`to`/`at`, a flat string `sourceRef` for the schema's object). So the first
schema-valid op raised `TypeError: … unexpected keyword argument 'from'`. It now goes
through `publisher_structure.overrides.parse_overrides`: strict (an unknown field is an
error, not dropped), it requires `schema: "overrides/1"`, and it checks the schema's
per-op requirements. Each mapping table is pinned to the schema file by a test. A
malformed log is `BAD_INPUT` with an `override-log-malformed` diagnostic. `resolve` is
at v3.

**Worker — done.** `worker._override_log_for` reads the manuscript's `override_ops` in `seq`
order, writes them to the CAS as one `overrides/1` document (sorted-key JSON, so an
unchanged log has an unchanged hash and cache key), and supplies it as `resolve`'s
`overrides_path`, only when there are ops. Migration `005` grants `publisher_worker`
`SELECT` on `override_ops`.

**The loop is plumbed end to end, and no override can take effect yet.** Ops target nodes
by `sourceRef.docxId`, and `docx_to_ast` emits **no per-node `sourceRef` at all** (verified:
an ingested AST carries none; the only `sourceRef` is the document-level one). Worse, an op
whose target matches nothing is **silently skipped**: `apply_overrides` returns the AST
unchanged, and `_rewrite` has no notion of "found nothing". So every stored op reaches
`resolve` and does nothing, with no error, no warning and no metric. That's the same failure
`dc6a6f3` fixed for unimplemented ops, one layer over.

Making an unmatched op fatal, as `dc6a6f3` did, has a trap here: the log is append-only
with no undo, so one op aimed at a node that doesn't exist would leave that manuscript
unbuildable for good. And a non-fatal report has nowhere to go: `StageResult.warnings` is
read by nothing (not the executor, the worker or the API), and the API doesn't return
per-stage `metrics`.

**Ingest ids — done.** `docx_to_ast` now tags every chapter and block node with a
`sourceRef.docxId`, which is exactly the set of types the schema allows it on (pinned by a
test). The id is content-derived, not positional: a positional id shifts for every node
after an insert, so a stored op would silently land on a *different* paragraph. Chapters
are keyed by title, so editing prose doesn't orphan a retitle. Repeated content is told
apart by occurrence order. `ingest` was then at v4. An end-to-end test takes a real DOCX,
ingests it, retitles its chapter through `resolve`, and checks the title changed.

**Orphaned ops — reported, not fatal (decided).** Fatal plus an append-only log would leave
a manuscript unbuildable for good. `GET /v1/manuscripts/:id/structure` now returns
`orphanedOps`, in `overrides/1`'s own shape (`{op, reason: "no_source_ref"}`): every stored
op that targets no node in the manuscript's latest AST. `orphanedOps` is `null` when no build
has produced an AST yet (unknown, not "none"). The check (`orphanedOps` in `manuscripts.ts`)
mirrors `overrides._matches`/`_CHILD_KEYS` exactly, so an op is reported exactly when
`resolve` would skip it. `resolve` itself still skips orphans silently inside the build;
the report lives where the reviewer looks, not in the build log.

**Still silent:** an op that matches a node but can't act on it. A `retitle` aimed at a
node with no `attrs.title` (a paragraph) is returned unchanged by `_t_retitle`, and nothing
reports that either.

**Node ids — done.** `GET /v1/manuscripts/:id/structure` gives every chapter and every
`lowConfidenceNodes` entry a `docxId`: the id an op must carry to target it, or `null` when
nothing can (a front/back-matter section wrapper — the schema gives it no `sourceRef`, only
its contents — or any node of a pre-v4 AST). The view and `orphanedOps` share one
`docxIdOf`, and a test pins that every id the view shows is one an op lands on.

**Review page — done, read-only.** `/manuscripts/[id]/review` (`packages/web/src/app/`)
renders the panel from a Server Component. The API needs a bearer token, so the page fetches
with `PUBLISHER_API_TOKEN` (not `NEXT_PUBLIC_`, so Next never inlines it into a client bundle)
and the browser gets only the result. A stub-API smoke run checked the token appears in
neither the HTML nor the RSC payload. `packages/web/src/types.ts` is now exactly what the
API returns: the panel read `ChapterReview.id` and `.ambiguities`, which the API never sent,
so it would have thrown on first render. `OverrideOp` is now `overrides/1`'s shape. The panel
shows each node's target id, the logged ops aimed at a chapter, and the orphaned ops, and
says so when a manuscript has no build yet.

**Writing ops — done, unauthenticated by decision.** A chapter with a target id gets a form
that logs a `retitle` or `flag_ambiguity` through a Server Action (`src/review/actions.ts`).
The action builds the whole op (id, actor, timestamp, shape) from a target id and a text
field. The browser asserts none of it, so an off-schema op can't be sent. It PATCHes with the
server-side token and the op id as `Idempotency-Key`, then `refresh()`es the page. An API
refusal or an unreachable API shows in the form rather than crashing the page. A smoke run
against a recording stub validated the ops it sent against `overrides/1` and through
`resolve`'s parser. The old browser `submitOverrides`, which sent no token and could never
have worked, is gone. `delete` and `reclassify` are left off the form: one is irreversible
in an undo-less log, the other needs a node-type picker.

**No reviewer authentication (decided: local dev only).** A Server Action is a public POST
endpoint, and this one writes as the tenant. `npm run dev`/`start` bind `127.0.0.1`,
verified unreachable on the LAN address. That is the only protection: a standalone
deployment (`node .next/standalone/server.js`) ignores `-H` and binds per `HOSTNAME`. Every
op is attributed to one actor, `user:local-reviewer`. Before the UI goes anywhere but
localhost it needs sign-in, and with sign-in per-reviewer actors.

Also: the log has no undo. Ops are immutable and the schema has no revert op, so a
reviewer's mistake can only be superseded by a later op, never removed.

### 1.2 Nothing dispatches the agent tools

`services/agents/publisher_agents/runtime.py:286` calls `_run_agent_logic`, which at
`:305` branches on role and computes its output **directly from `inputs`**. The
`tools=[...]` argument is used only for a length check against `budget.max_turns` and
recorded on `AgentCall.tools_used:58`. `registry.call` is never reached from `execute`.

`23adc58` made the five tools answer honestly from artifacts they are handed. It did not
make them reachable. Separately, **no stage constructs any agent class** — grep for
`StructureWrangler|Compositor|PreflightExplainer|AgentRuntime` across `stages/` returns
nothing. The agents package is dead from both ends.

**Work:** turn `_run_agent_logic` into a real tool-dispatch loop (the docstring at `:311`
says this is where the LLM call belongs), and decide whether an `agent-propose` stage
exists at all. Large. Do not start it as a wiring task — it is a design task.

### 1.3 LLM classification is computed and thrown away

`stages/structure_infer_stage.py:47` declares `terminal_outputs=["classification"]`.
The stage makes a real OpenRouter call, parses a real response, writes a `classification/1`
artifact — and **nothing consumes it**. The stage's own docstring (`:18`) calls it
"advisory input", which is accurate and also the problem: nothing takes the advice.

Upstream, confidence never reached the AST at all. **Now fixed — see below.** What the
survey got wrong about *where*: it blamed `ast-assemble` and pointed at
`rules.classify_blocks`. In fact the real AST is built by
`services/ingest/publisher_ingest/docx_to_ast.py`, which never ran `rules.py`: it makes
its own structural decisions (heading from style and/or capitals, front/body boundary from
a prose threshold, back matter from a pattern) and recorded none of the evidence.
`ast-assemble` only passes that AST through. And `ast.schema.json` sets
`additionalProperties: false` on every node with no `confidence` field anywhere, so
emitting one would have failed validation. `rules.build_ast_draft` *does* emit confidence,
but it's schema-invalid and called only from tests.

**Done:** `ast.schema.json` declares an optional `confidence` on `chapter` and the
front/back-matter section objects (absent = not measured, never defaulted). `docx_to_ast`
scores each decision where it's made: styled and upper-case 0.95; style alone 0.85;
**capitals alone 0.7, below the 0.8 escalation line**, since a shouted "NO!" looks the
same; multi-line titles take the weakest line; front matter carries the boundary's
confidence (0.85 found by prose, 0.5 fallback); back matter by pattern 0.9. `ingest`
stage bumped to v3. `query_nodes` now filters only types that can carry a score.

**Also done:** the API's `GET /v1/manuscripts/:id/structure` reads those scores through
`structureView` (`packages/api/src/routes/manuscripts.ts`) instead of hardcoding
`confidence: 1.0` and `lowConfidenceNodes: []`. An unscored chapter reports `null`, not 1.0.
`lowConfidenceNodes` lists every section below 0.8 across all three roots, and is `null` —
not `[]` — when the AST carries no scores or no build has run. The web contract
(`packages/web/src/types.ts`) and panel were brought in line.

**Still open:**
- The six `corpus/manuscripts/*.ast.json` predate this and carry no scores. They're valid
  (the field is optional), but they exercise only the unscored path.
- Nothing consumes `classification/1` (probably `resolve` should, as proposed overrides).

### 1.4 Both Rust crates are unreachable from Python

`platform/pagescan/src/lib.rs:442` says PyO3 exports live "in a separate file
(`lib_py.rs`)". **That file does not exist** — `platform/pagescan/src/` contains only
`lib.rs`. `Cargo.toml` has no `pyo3` dependency and no `pyo3` feature.

`platform/cas/` is the same story from the other side: `platform/cas/src/lib.rs` is a Rust
implementation, `platform/cas/py/publisher_cas/` is a **separate pure-Python
implementation** (`hashlib`, `:10`), and `platform/cas/ts/` is a third. Nothing in
`packages/` imports the TS one. Three implementations of one content-addressed store, with
no cross-implementation agreement test.

CI does run `cargo test` and `cargo clippy -- -D warnings` (`ci.yml:119-120`), so the Rust
is correct — it is just not on any path.

**Work:** either build the PyO3 binding and delete the duplicated logic, or delete the
unreachable crates and the comment that promises a file that was never written. The second
is smaller and more honest. If the crates stay, a cross-implementation test (same input,
same digest / same defects) is the minimum.

---

## Tier 2 — measurement gaps (a gate exists but cannot see)

### 2.1 Rivers and hyphen stacks are no-ops

`platform/pagescan/src/lib.rs:246` (`detect_rivers`) and `:253` (`detect_hyphen_stacks`)
take their arguments, discard them via `let _ = page;`, and push no defects. Both are
called from `scan()` at `:137-138`.

This is *correctly* deferred, not a bug: both genuinely need glyph positions from a
rendered raster, which nothing in this repo produces. It is listed here so nobody reads
`scan()`'s call list and concludes these are covered. Note it is also currently moot —
per 1.4, nothing calls `scan()` from Python at all.

**Work:** blocked on a page-raster pipeline. Do not attempt from pagemap hints; a
heuristic river detector that fires on layout metadata is worse than none, because it
would produce a `false` where the truth is "unknown".

### 2.2 Missing preflight checks

`services/prepress/publisher_prepress/preflight.py` implements eleven checks: trim size
(`:115`), bleed (`:150`), min/max pages (`:175`, `:200`), page multiple (`:225`), colour
space (`:251`), resolution (`:268`), file size (`:296`), font embedding (`:317`), PDF
standard (`:335`), composition (`:414`).

Not implemented, all of which real POD vendors reject on:

- **Total ink coverage** (KDP/IngramSpark cap ~240–300% TAC). Needs per-pixel CMYK sampling.
- **Rich black / registration black** in body text — a 4-colour black at 9pt is a reprint.
- **Transparency and live blend modes** surviving into PDF/X-1a.
- **Annotations, form fields, embedded JS** — must be absent in a print PDF.
- **Spine width vs. actual page count and paper stock.** `geometry.py:69` computes spine
  from a `paper_basis` default; no profile supplies a real per-vendor stock caliper.
- **Gutter/creep** for the bound edge at high page counts.

Related profile gap: **no profile file declares a `composition:` block**, so the gate added
in `65f5834` runs entirely on its `maxOrphanPages=0` default. Verified against
`profiles/kdp/us-trade.yaml` — it carries trim, bleed, pdfSpec, coverSpec, proofSpec,
min/max pages, page multiple, and nothing else. There is also no `maxInkCoverage` for 2.2
to gate against when it is written.

**Work:** ink coverage and rich black are the two that actually cause rejections; both
need a rasterizer, which Ghostscript already provides. The annotations/JS check is nearly
free — `probe_pdf` (`:612`) already opens the file.

### 2.3 Eight override ops are declared but unimplementable

`services/structure/publisher_structure/overrides.py:357` — `_TRANSFORMS` implements four
ops (reclassify, retitle, delete, flag_ambiguity). `:368` — `UNIMPLEMENTED_OPS` names the
other eight the schema accepts: `split`, `merge`, `promote`, `demote`, `insert`, `rename`,
`set_attr`, `resolve_ambiguity`.

Since `dc6a6f3` these fail the build loudly instead of vanishing, and a test asserts the
two sets partition the schema's enum. So this is *safe*, not silent — but a user editing a
book still cannot split a run-on chapter, which is the single most common real fix.

**Work:** `split` and `merge` are the two worth writing. `set_attr` is nearly free and
subsumes `rename`/`retitle`.

---

## Tier 3 — never built

### 3.1 Real DOCX — Word-authored fixtures, and a first real manuscript

**Done: Word's own XML.** `corpus/word/` holds three DOCX files that Word wrote, authored
through its COM API by `corpus/word/make_word_corpus.py` (Windows + Word only; the files are
committed and CI only reads them). Between them they carry tracked insertions and deletions,
a comment, a footnote, endnotes, a Word TOC field, a hyperlink, a content control, a nested
table, a text box, non-breaking and soft hyphens, a section break, Greek text and an
equation. `services/ingest/tests/test_word_corpus.py` runs ingest over them.

The first run found five places where ingest **succeeded and lost text**: tracked
insertions (`w:ins`), content controls (`w:sdt`, inline and block), nested tables, text boxes
and endnotes. The no-loss post-condition missed all five for one reason: its "source" list
came from the same walk that built the AST, so text the walk couldn't see was missing on
both sides. It now reads the XML directly (`docx_rich.source_texts`): every `w:t` in the
body and note parts counts unless it's in a deletion, a move source or the VML fallback copy.
A test checks that blinding the walk to `w:ins` makes ingest fail. The first run also found
two structure bugs:
- A Word TOC's entries (style `toc 1`) and an upper-case title page were adjacent heading
  candidates, so they merged into chapter one's title. TOC entries are no longer headings,
  and a page or section break now separates headings.
- Equations (`m:oMath`) are refused with an `IngestError` instead of vanishing, since no
  renderer has an equation case.

**First real manuscript (2026-09-24).** A Greek academic book: about 3,900 paragraphs, 477
footnotes, 2 SmartArt diagrams, 5 images. It is **not committed**: this repo is public and
the book isn't ours to publish, so `.gitignore` excludes `corpus/*.docx`. What it taught is
reproduced with synthetic prose in `corpus/word/word-thesis.docx` and pinned in
`test_word_corpus.py`. Against v5 it failed in six ways:
- **Refused outright.** A footnote holds a photograph, and notes had no media sink. A note's
  pictures now become `figure` blocks right after the footnote, because `footnote` content is
  inline-only.
- **The whole book was one chapter.** Its headings are bold Normal-style lines numbered by
  hand ("4.3.1 …", up to 147 characters). They're neither upper-case nor `Heading`-styled,
  so neither signal fired. The only `Heading` styles were on **bibliography entries**, which
  became chapters 2 and 3, titled with citations. Now:
  - A fully bold line with a typed section number is a heading. Depth 1 opens a chapter;
    deeper becomes a `heading` node in place.
  - Bold plus a known section name (Πρόλογος, Βιβλιογραφία, Contents, …) is a chapter-level
    heading.
  - Once back matter starts, later headings stay inside it, one paragraph per entry.
  - Title patterns are matched accent-folded. "Περιεχόμενα" never matched `ΠΕΡΙΕΧΟΜΕΝΑ`,
    because ό doesn't fold to Ο.
  - Back matter gets its type (`bibliography`, `index`, `appendix`), not always `colophon`.

  The book now comes out as 15 chapters, a contents page and the bibliography, matching the
  author's own contents page, including all 19 section headings.
- **SmartArt text was lost unseen.** A diagram's words live in a separate data part, so
  neither the walk nor the no-loss oracle read them. Both now do (`diagram_texts`), and the
  diagram becomes a sidebar of its labels. The layout is lost.
- **EMF.** An 8.7 MB EMF figure became a figure no renderer draws and an AST the schema
  rejects. Ingest now refuses any image outside `mediaRef`'s types, by file name.
- **Slow:** 45 s. python-docx re-derived the default style for every unstyled paragraph. Style
  names are now resolved once, and it takes 8.6 s.

With its one EMF re-encoded as PNG (a local copy), the book ingests and the AST is
schema-valid. `ingest` is at v6.

**Through the pipeline (same day).** `python tracer_bullet.py <book>.docx "Greek 17x24"` now
runs a Word file through the real `ingest` stage. The book (a local copy with its EMF as PNG)
rendered **858 pages**. Two real bugs surfaced, both fixed:
- **The text-integrity gate failed a faithful book.** `ast-assemble`'s source side put a
  space between every text node, while the HTML renders marked runs back to back
  (`(<em>᾿Επίκτητος</em>,`). So wherever formatting met punctuation, it read "( word ,"
  against "(word,", and failed at offset 262 with no text lost. Text nodes inside a block
  are now joined with no separator, and spaces go only at block boundaries (v4). A dropped
  italic word still fails the gate, pinned by a test.
- **No chapter or front/back-matter section ever started on a recto.** The CSS said
  `page-break-before: recto`. That property takes only left/right in CSS 2, and weasyprint
  drops `recto` silently (measured: 1 page where `break-before: recto` gives 5). Chapters
  still opened on a fresh page only because their named `@page` changed. The book's contents
  page ran on from its epigraph mid-page. `emit_css` now writes `break-before`
  (`design-compile` v4), and `stages/tests/test_page_breaks.py` renders and checks every
  section lands on an odd page.

**Still:**
- **This book needs its EMF figure ("Εικόνα 6") re-saved as PNG** before it can build.
- **Preflight verdict (2026-09-26, in the worker image, weasyprint 70): PASS, with 2
  warnings.** 9 checks pass and none fail. The book was packaged at 869 pages; it's 821
  now that `footnote-policy: line` is gone (819 on 62.3; see **weasyprint 70** below).
  - Warning 1 was composition: 27 widow pages and 264 runt pages. Both are now 0 (see
    **Runts** and **Widows** below).
  - Warning 2: image resolution is unmeasured (no PDF image decoder).

  The first verdict, earlier the same day, was FAIL on 4 orphans (pages 208, 253, 284,
  660). They were real, and not a CSS problem:
  - **The cause.** The notes are long Greek quotations, up to 71 lines. weasyprint 62 can't
    break a note across pages, so a note that nearly filled a page left the body one line.
    weasyprint won't leave a page with no body text, so it broke `orphans: 2` there.
  - **Splitting long notes.** A note over `NOTE_SPLIT_CHARS` is now set in sentence-end
    pieces (`stages/rendering.py` `PrintNotes`). weasyprint can carry the pieces over, and
    the footnote area is capped at 60% (`NOTE_AREA_MAX`).
  - **Numbering.** The renderer numbers notes itself: CSS draws `attr(data-n)` on an empty
    `note-call` span. weasyprint's own call belongs to the paragraph and printed a number
    for every piece ("5555").
  - **What was measured before choosing this.**
    - A cap without splitting makes page-long notes run past the type area, which is real
      loss; measured at 117–462 lines on a synthetic book.
    - Splitting without the cap still leaves pages with one body line.
  - **The integrity gate caught two bugs in the splitter on the real book.**
    - A cut between a "(" and a link inserted a space.
    - Negative room let a "not found" pass the break test.
  - **A dead CSS rule.** The rule above the notes was never drawn: the CSS said
    `@footnotes`, which matches nothing. It now says `@footnote`.
  - **Typst.** Its Lua filter folds the pieces back into one `#footnote`, since Typst
    breaks notes itself.

  Everything before preflight also had to be fixed to get this far:
  - **Memory budgets were too small (only Linux enforces them).** `ingest` needs 135 MB
    against a 128 MB budget; `paginate` needs 328 MB against 256. Both failed the book in the
    worker image, and nothing noticed on Windows. Now 512 and 1024 MB, measured (`VmPeak`
    minus the stage's starting size). Every other stage is well inside its budget.
  - **`finish-gs` turned every page into a picture.** One figure had an alpha channel.
    weasyprint puts its soft mask in the resources every page shares, and PDF/X-1a (PDF 1.3)
    cannot carry transparency, so Ghostscript rendered all 805 pages as 300 dpi images. There
    was no text left, 1.6 MB and 6 s a page, and it still passed every check as "PDF/X-1a".
    - Print figures are now composited onto white (`stages/media.py` `opaque`).
    - `to_pdfx` now refuses a press file with under half its input's text-drawing
      operators (`TEXT_KEPT_MIN`), so anything else that rasterizes pages fails loudly. On
      the real book that's 33,724 of 38,271 kept (0.88); a rasterized page keeps about 3%.
      Counted in Python in about 5 s. The first version extracted the text with Ghostscript's
      `txtwrite`, which took 558 s per file.
  - **Hyperlinks voided PDF/X.** Link annotations aren't allowed on a PDF/X page, and the
    book's 200-odd links made Ghostscript fall back to plain PDF. The existing downgrade
    check caught it. The press conversion now drops annotations (`-dPreserveAnnots=false`);
    the proof keeps them.
  - **Deadlines were too short.** The press conversion alone takes 430 s; the old 300 s
    killed it. Ghostscript now gets 2400 s (`GS_TIMEOUT_S`) and the finish stages 3600 s.
    The passing runs' `finish-gs` took 1884 s and 2313 s, each while other work shared the
    CPU (the second alongside the full test suite).
    The whole of `finish-gs` took 1293 s, most of it the two text extractions, since replaced.
    The proof (`/ebook`) is the slowest remaining step at about 2.5 s a page. Linearization
    isn't the cause: it makes no measurable difference. It isn't run beside the press
    conversion either: the POSIX sandbox sets its limits in `preexec_fn`, which Python
    documents as unsafe from threads.
  - **The worker image would have held the manuscript.** `COPY corpus/` copied whatever sat
    in `corpus/`; `.dockerignore` now excludes `corpus/*.docx` and `*.doc`, like `.gitignore`.
  - **A refused book's verdict was invisible.** The report was written to CAS, but only a
    stage that succeeds had its artifacts recorded, so `GET /v1/builds/:id/preflight`
    answered "pending" forever for a book preflight had refused. `StageError` now carries
    `artifacts`, `preflight` puts its report there, and `worker.py`'s `_on_stage_error`
    records it. Warnings print as well as failures. The Temporal path still loses
    `StageError` details at the activity boundary, a gap already documented for its error
    kinds.
- **Fonts — done (the house faces chosen 2026-09-24: GFS Didot, PN Katsoulidis, Minion
  Pro).** Before this, no book was set in its designed face. Every template asked for EB
  Garamond, it was installed nowhere, and the renderer silently substituted.
  - **What's in the vault.** All three faces are registered in `fontvault`. All three cover
    the book's 120 polytonic characters.
    - GFS Didot is OFL. It's the default for the built-in spec and every Greek preset, and
      it's in the worker image (`fonts-gfs-didot`).
    - Minion Pro (Adobe) and PN Katsoulidis (fonts.gr) are commercial. They're licensed for
      print PDF only, not EPUB embedding. They're supplied through `PUBLISHER_FONT_DIRS`
      (compose mounts `PUBLISHER_LICENSED_FONTS` at `/fonts/licensed`) and never committed.
  - **How rendering uses them.** `paginate` resolves each family to files
    (`fontvault.font_faces`, keyed by name ID 1, since GFS Artemisia's bold italic claims
    typographic family "GFS Didot"). It pins them with `@font-face` and embeds them in full:
    PN Katsoulidis's fsType forbids subsetting.
  - **What happens when one is missing.** A family that isn't installed fails the render
    (`INFRA`) instead of being substituted. Glyphs the face lacks are filled by other fonts,
    and those are reported (`fallback_font_count`). For this book that's Arial and Verdana,
    for ʼ ― ∙ and a few combining accents.
  - **Result.** The book renders in GFS Didot: 813 pages, 165 s.
  - **Still:**
    - The other templates (Literary, Thriller, Memoir, …) still name faces nobody installs
      (EB Garamond, Source Serif, Libertinus, Noto, Merriweather). They now fail loudly
      instead of substituting.
    - The Typst path doesn't use the vault's files yet.
    - The font manifest's hashes are still identity hashes, not file hashes.
- **Footnote calls — fixed.** A note's `<span class="footnote">` used to render after its
  paragraph's `</p>`, so weasyprint set the call number alone on a line of its own. That hit
  every note, all 477 in this book. Notes now render inside the paragraph that cites them
  (`extract` v2, `paginate` v9, `idml` v2), so the call sits against the last word. A
  leading space inside the span keeps the integrity gate's text stream separated ("text.
  Note"), then collapses in the footnote area. The first attempt without it failed the gate
  on the real book. The Typst test's `"emphatic1" not in text` had passed only because of the
  misplacement. It now asserts the call starts where the cited word ends, on the same line.
  The book is now 805 pages.
- **EPUB — fixed (2026-09-24): the whole book, checked, and EPUBCheck-clean.** It used to
  hold 76% of the text: its writer had its own renderer that dropped every footnote, table,
  figure, sidebar and front/back-matter section and every mark, and reordered words around
  inline elements. Nothing checked it.
  - **Rendering.** The content documents come from `stages/rendering.py`
    (`ast_to_epub_sections`), the renderer the print gate verifies. Footnotes are the one
    difference: a `noteref` link in the citing paragraph and an `aside epub:type="footnote"`
    right after it, numbered through the book. `publisher_epub` only packages.
  - **The check.** The `epub` stage reads the written package back (`spine_text`, which skips
    the generated note calls) and compares it with the document's text stream. That stream is
    now shared with `ast-assemble` (`stages/text_stream.py`). A mismatch fails the stage
    (`ENGINE_BUG`, `integrity_mismatch`) and nothing is stored.
  - **Package fixes.** The mimetype entry is now first and stored. The OPF now has
    `dc:identifier`, `dc:title`, `dc:language` and accessibility metadata. Zip timestamps are
    fixed, so the same book gives the same bytes. Images come out of CAS; TIFF and BMP are
    converted to PNG.
  - **Found on the way:**
    - The print gate skipped figure and table captions and epigraph sources, though the HTML
      prints them. The first captioned figure would have failed the build. Fixed in the
      shared stream (`ast-assemble` v5).
    - Word hyperlinks with raw `\` and `|` (the book's 12 Perseus links) were invalid URLs in
      both the EPUB and the PDF's link annotations. They're now percent-encoded in the shared
      renderer.
  - **The real book:** 19 content documents, 477 linked notes, 4 images, 2,056,296
    characters verified, in 14–20 s. **EPUBCheck 5.4.0: 0 errors, 0 warnings.**
    `test_epubcheck_accepts_the_epub` runs EPUBCheck when `PUBLISHER_EPUBCHECK_JAR` names a
    jar, and skips otherwise.
  - **Still:**
    - ACE by DAISY (the other §3.12 gate) hasn't been run.
    - Fonts aren't embedded: GFS Didot could be (OFL), but the commercial faces can't.
    - EPUBCheck isn't in CI.
- Its diagrams drawn from VML lines and arrows (58 `w:pict` shapes) keep their text, but the
  lines and arrows are dropped.
- Captions typed as prose ("Εικόνα 6: …") aren't attached to their figures.
- Bold inherited only through a style isn't seen by the bold-heading rule; this book sets it
  directly.
- Validating the book's AST takes about 22 s. It's linear now, but Python `jsonschema` is slow
  per node.
- Equations need an OMML → MathML path and a renderer case before a STEM book can build.
- Endnotes become footnotes (the AST has no endnote node), so their text survives but they
  print at the page foot.
- `w:sym` (Symbol/Wingdings characters) is still unread and still unchecked.
- **`ast.schema.json` validation — fixed, now linear.** The five AST unions (`bodyNode`,
  `blockNode`, `inlineNode`, `frontMatterNode`, `backMatterNode`) were `oneOf`, which
  `jsonschema` evaluates in full, every branch at every depth. That was exponential in
  nesting: the Word novel took ~5 s, and the technical book (a table in a cell) didn't finish
  in minutes, as `python cli.py schema validate` wouldn't have. Each union is now an `allOf`
  of `if`/`then` on `type`, plus a `type` enum that rejects unknown types. The accepted set is
  unchanged: every branch is a closed object that requires `type`, and the branches' `type`
  enums are disjoint. A differential run of the old and new schemas over 400 random
  mutations of valid Word ASTs (109 still valid, 291 not) agreed on every verdict. Both Word
  ASTs now validate in about 0.3 s. `schemas/codegen` reads
  the shape as a union (`unionBranches`), and it emits byte-identical Zod and Pydantic to
  before. `test_word_corpus.py` pins the timing, a rejected invalid node inside a nested table,
  and that each union's enum equals its branches' types.

`corpus/raster-diff/compare.py` exists; nothing in CI invokes it.

### 3.2 CVE gate is structurally incapable of failing

`platform/supply-chain/publisher_supply_chain/__init__.py:88` — `CveGate.scan` iterates
`sbom._dependencies` and looks each up in `self._known_cves`, a dict initialised empty in
`__init__` and never written to. The findings list is therefore always empty and `passed`
is always `True`.

This is **honestly disclosed** at `ci.yml:160-162` ("CveGate is a placeholder"), and the
real gates in that job are `pip-audit --require-hashes` and `npm audit --audit-level=high`,
both of which do fail. So the risk is documentation, not security.

**Work:** either populate it from Trivy/Grype as the docstring says, or delete it and let
pip-audit/npm audit be the whole story. Deleting is smaller.

### 3.3 `packages/web` in CI — built and type-checked; still no tests

**Done.** CI has a `web` job: `npm ci` then `npm run build`. `next build` runs TypeScript over
the whole app and compiles every route, and it needs no environment (the review page is
dynamic, so the API isn't called at build time). Checked from a fresh clone, so it depends
only on tracked files (`next-env.d.ts` and `.next/` are gitignored). Reintroducing the
panel's old read of `ChapterReview.id` fails it with `TS2339`. The step is classified in
`tests/meta/test_gates_can_fail.py` like the API's `tsc`.

**Still:** `packages/web` has zero test files. `actions.ts`'s input checks and the panel's
rendering were verified only by a manual stub-API smoke run.

### 3.4 No scheduled CI

`.github/workflows/ci.yml:11-17` triggers on `push` and `pull_request` only. There is no
`schedule:` and no `workflow_dispatch:`. `platform/reproducibility/` implements a full
`ReproducibilityChecker` (manifest compare, toolchain compare, byte compare) that nothing
runs periodically — and byte-reproducibility is precisely the property that decays from
*outside* changes (a base image bump, a font update), which push-triggered CI cannot see.

**Work:** a nightly `schedule:` running the reproducibility checker. Small.

### 3.5 Inference is wired but incomplete

`services/structure/publisher_structure/inference.py:257` — `OpenRouterProvider` is a real
network client (auth, retry on 429/5xx, response parsing). Its own docstring (`:268-273`)
names what it deliberately does not do:

- no `response_format` structured-output enforcement — the model can return prose and the
  parse will fail at runtime rather than being prevented;
- no reasoning-effort or provider-pinning, though `platform/routing/policy.yaml` has a
  schema for both;
- `PromptCacheManager` still returns a placeholder prefix, so prompt caching saves nothing.

**Work:** `response_format` first — it is the one that converts a class of runtime failures
into impossible states.

---

## Not missing (so this stops being re-investigated)

- **Temporal.** `worker_temporal.py` and `platform/orchestration/py/` exist and work; the
  path is opt-in behind `docker compose --profile temporal`. Its tests are marked
  `requires_temporal` and deselected by default, by design.
- **epub / onix / manuscript-advisory / idml.** All four are real registered stages
  (`stages/secondary_output_stages.py:46`, `:85`, `stages/advisory_stage.py:35`,
  `stages/idml_stage.py:44`). `idml/1` being terminal is deliberate, documented, and
  correct — InDesign composes at open time.
- **Cover pipeline.** Four stages plus geometry and preflight, with a real OpenRouter image
  adapter (`services/cover/publisher_cover/image_gen_port.py:81`).
- **`finish-gs` failing locally.** Ghostscript 10.07.1 toolchain mismatch, documented in
  `a31c9bb`. Not a code defect.
- **`allow_stub_engines`.** Dev-only escape hatch, defaults `False`, worker never sets it.
  Working as designed.

---

## Build and test status (2026-09-25): every CI job, run locally

Every job in `.github/workflows/ci.yml` was run on the author's machine; each passes.

- **python.**
  - The default suite: 661 passed, 0 failed, 12 skipped, now against a real Postgres. The
    30 database tests had errored for as long as a foreign Postgres held port 55432. They
    run on a throwaway one on 55433 (the **publisher-tests** skill has the recipe).
  - Every skip was run where it can run:
    - The 10 POSIX-only tests (rlimits, the sandbox, process groups, SIGTERM): 51 tests
      across their files pass in the worker image on Linux.
    - The EPUBCheck test passes with `PUBLISHER_EPUBCHECK_JAR` set.
    - The `pip-audit` meta-gate passes on Linux. That test reads any `--ignore-vuln`
      flags from `ci.yml`, so it runs the command CI runs (there are none now).
  - The `external` marker passes, 1 test. `requires_temporal` passes, 2 tests.
  - The first real book, end to end in the worker image on this code: preflight passes
    with 2 warnings, a 7.9 MB PDF/X-1a, and the book is packaged.
- **node.** `packages/api` passes the type-check and vitest, 126 tests.
- **web.** `next build` type-checks and compiles both routes.
- **rust.** `cargo test` passes and `cargo clippy -D warnings` reports nothing.
- **contracts.**
  - Codegen passes: 257 types import cleanly.
  - DAG integrity passes.
  - The contract tests pass, 38.
  - All six lints pass: stage versions, service deps, import boundaries, tenant scoping,
    docs claims, schemas.
- **supply-chain.**
  - `npm audit --audit-level=high` passes for both packages.
  - `pip-audit` passes, with no advisory accepted (weasyprint 70, below).

Bugs these runs found, all fixed:

- **Integration fixtures never worked together.**
  - `test_worker_override_log.py` imports `worker` at collection. That froze its
    `CAS_ROOT` before the integration fixture pinned it, so every in-process build failed
    "not in CAS". The fixture now reloads the module.
  - A module-scoped fixture asked for the function-scoped `make_docx`, which made all six
    override-log tests setup errors. `make_docx` is now session-scoped.
- **The worker died on a SIGTERM during start-up.** It exited -15 instead of 0, because
  the handler was installed only after `import stages` (seconds of weasyprint loading).
  It's now installed first, when run as a program.
- **`packages/api` had two high-severity advisories:** `fast-uri` and `nanoid`. Fixed with
  `npm audit fix`, within the declared ranges. One moderate `@vitest/mocker` advisory
  needs a breaking upgrade and stays.
- **weasyprint 62.3 had three advisories** (SSRF through redirects, CSS injection through
  presentational hints, and a fetcher bypass through `write_pdf` arguments). None was
  reachable from `paginate`, which renders through `local_only_fetcher`
  (`stages/tests/test_render_fetches.py` still pins that).

## weasyprint 70 (2026-09-26)

`requirements.txt` pins `weasyprint>=70,<71`; the lock carries 70.0 and pydyf 0.12.1.
`pip-audit` is clean, so `ci.yml` accepts no advisory any more.

- **What changed in code.**
  - 70 takes a `URLFetcher` instance, not a function, and `default_url_fetcher` is gone.
    `local_only_fetcher` is now a `URLFetcher` subclass that overrides `fetch`.
  - The private box tree (`_page_box`, `LineBox`, `.element`) the pagemap reads is
    unchanged; measurement works as before.
  - On Windows, 70 loads GTK only from `WEASYPRINT_DLL_DIRECTORIES`, not from PATH.
    `scripts/test.ps1` sets it to the first PATH folder holding Pango.
- **The regenerated lock also picked up `temporalio`.** `requirements.txt` has listed it
  since R1, but the old lock didn't, and both Dockerfiles install only the lock, so the
  Temporal worker image never had it.
- **Footnotes: 70 moves notes to protect orphans, and can print them out of order.** The
  real book (same input, measured in the worker image on an idle machine):

  | | pages | render | notes not on their call's page | notes in order | widows |
  |---|---|---|---|---|---|
  | 62.3 | 819 | 480 s | 150 (up to 5 pages away) | yes | 0 |
  | 70, default | 821 | 586 s | 169 | **no** | 0 |
  | 70, `footnote-policy: line` (5a2a4b0, withdrawn) | 869 | 720 s | 53 | yes | 27 |
  | **70, default + `notes_in_document_order`** | **821** | **289 s** | 174 | **yes** | **0** |

  - **The first fix, `footnote-policy: line`, was withdrawn.** It kept notes on their
    call's page by moving the call's line to the next page. But while any note was waiting
    to carry over, weasyprint 70 moved every later call line too. That left 23 pages
    holding one line of text, and the stranded last lines were the 27 widows.
  - **The fix now is `paginate_stage.notes_in_document_order`,** two hooks into weasyprint
    that sort by each note piece's `data-seq` (`rendering.PrintNotes`):
    - the carry-over queue, before each page lays it out (`make_page`);
    - each page's own notes (`LayoutContext._update_footnote_area`).

    The queue is where 70 reverses notes it moves to protect orphans/widows. The hooks only
    sort when every note has a `data-seq`, so any other document lays out untouched.
  - Near-empty pages (under 300 characters) are back to 17, the same set as 62.3's: front
    matter and chapter-end pages.
  - 62.3 also left 150 notes off their call page, which nobody had measured. On 70 it's
    174; a note carries over as on 62.3, never ahead of its call.
  - `test_long_footnotes` asserts the order, no widows, and no note ahead of its call.
- **Deadlines.**
  - `paginate` rises from 1200 s to 2400 s. With `footnote-policy: line` it took 720 s on
    an idle machine and 940 s beside the test suite; without it, 289–530 s.
  - `finish` and `finish-gs` run two Ghostscript passes, each allowed `GS_TIMEOUT_S`, but
    their deadline was 3600 s against 2 × 2400 s. It's now
    `FINISH_TIMEOUT_S = 2 × GS_TIMEOUT_S + 600`, with `GS_TIMEOUT_S` at 3600 s.
  - The reason: one afternoon the same Docker VM took 2333 s (press) and 1826 s (proof)
    on the 62.3 PDF, where 430 s was measured before, and 2292 s / 2006 s on 70's. That's
    the same per page for both, so weasyprint 70 doesn't slow Ghostscript; the VM was
    slow. The first 70 run of the book timed out in `finish-gs` at the old 3600 s. Hours
    later the whole stage took 1089 s. A deadline has to survive the slow afternoon.
- **The real book on 70, end to end in the worker image:** preflight passes, and the book
  is packaged. The first run had 0 failed and 2 warnings: 27 widow pages and 264 runt
  pages, both since fixed, and image resolution not measured. It's now 821 pages with
  no composition defect; see **Runts** and **Widows** below.
- **The weasyprint/Typst stage tests now pass on Linux** (worker image, 465 stage and
  service tests). CI skips them, and two had only ever passed on Windows fonts:
  - The orphan sweep stopped at 164 words, and the image's fonts need 180. It now sweeps
    a full page (`SWEEP`).
  - The Typst footnote call was checked as "1", where GFS Didot sets a true "¹". Both
    forms are accepted, and PyMuPDF is skipped only when absent.
- **Test isolation.** `test_api_drives_pipeline.py`'s `worker_process` fixture was
  module-scoped, so its worker outlived the test using it and sometimes claimed the next
  test's "no worker has run" build. It's now function-scoped.

## Runts (2026-09-26): 264 pages to 0

A runt is a paragraph whose last line holds one short word. Preflight warned on 264 of
the book's pages. weasyprint 70 has no `text-wrap: pretty`, so the print renderer
glues each ending instead (`stages/rendering.py`, `_keep_tail`). It wraps the last two
words in `<span class="keep">` (`white-space: nowrap`). The characters are the book's
own, so the integrity gate is unaffected, and the EPUB, which reflows, isn't touched.

Each pass on the real book, and what the one after it fixed:

| Pass | Runt pages | What was left |
|---|---|---|
| none | 264 | |
| last two words, same text run, ≤ 24 chars | 39 | last word in a run of its own (Word splits runs at every format change), Greek pairs of 25–28 chars, double spaces |
| across runs, any whitespace, ≤ 40 chars | 24 | note calls alone on a line, headings and titles, table cells, URL tails, one note split |
| calls kept with the last word; headings, titles, cells (≤ 16) kept; a long ending keeps its last 30 chars | 2 | a narrow cell measured against the page; a note piece cut right after a forced line break |
| per-block measure; note cut takes one more word after a hard break | **0** | |

Widows (27) and orphans (0) were unchanged, and so was the length (869 pages). Both came
from `footnote-policy: line`; see **Widows**.

Two measurement changes, both in `paginate`'s `_measure_pages`:

- **A runt is now a short one-word last line:** under `RUNT_MAX_FILL` (a quarter) of the
  measure. A URL breaks at its slashes, so its last line could be one "word" that filled
  most of the line. That's no runt, and 11 of the 24 were such lines. A short word alone
  still counts, which `test_runts` pins with a control that must produce runts.
- **Each line is measured against its own block** (a cell, a note), not the page. Against
  the page, a full line in a narrow cell read as a short one.

`stages/tests/test_runts.py` covers each case, and each rendered check has a control
showing it can fail.

## Widows (2026-09-26): 27 pages to 0

Every widow was a paragraph's last line, carrying its note calls, alone at the top of a
page. `footnote-policy: line` had moved that line over to keep its notes beside it. The
page before usually held that paragraph's single previous line and nothing else: 23
such near-blank pages. `widows: 3` changed nothing (27 widows, 4 more pages), because
this path ignores it. `footnote-policy: block` crashes weasyprint 70 on this book
(`assert not page_is_empty` in `make_page`). The policy is gone, and
`notes_in_document_order` fixes the out-of-order notes it was there to prevent (see the
table under **weasyprint 70**).

On the real book: 0 widows, 0 orphans, 0 runts, notes in order, 821 pages.

## If you do three things

1. **§3.1 — done for the first real book: it passes preflight.** It runs end to end in the
   worker image and is packaged. Its 821 pages (weasyprint 70) become a text-carrying
   PDF/X-1a, and the EPUB is EPUBCheck-clean. What's left:
   - No composition warnings: runts 264 → 0 and widows 27 → 0 (see **Runts**, **Widows**).
     The one warning left is image resolution, unmeasured.
   - The proof takes about 2.5 s a page.
   - The author must re-save one EMF figure as PNG.
2. **§1.3 — done.** Ingest scores its structural decisions, the AST carries them, and the
   API reports them without defaulting. What's left there is consuming `classification/1`.
3. **§1.1 — authenticate reviewers.** The loop closes: a reviewer can open
   `/manuscripts/[id]/review`, log a retitle or flag, and see it applied on the next build.
   But it's localhost-only by convention, with one shared actor.
