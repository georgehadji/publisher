# Compositor patches — from a verdict to a fix a reviewer accepts

**Status:** plan, 2026-10-13, against `18c2e7a`. **K1–K5 built** (2026-10-13/14). The loop is proven end to end on a Word book under a widow-permitting stylesheet; under the house stylesheet nothing widows or runts, so on the CSS path it is a safety net with nothing to catch today (see **Live check**, below). Owns identifier series **K0–K5**.
**Follows:** `WIRING_PLAN.md` W11 (the Compositor's caller, `layout-review`, landed in `18c2e7a`).

Today `layout-review` looks at each page preflight warned on and records `confirmed`,
`not-a-defect` or `needs-human`. A reviewer learns which warnings are real and can do
nothing about them. This plan adds the fix: a proposal per confirmed defect that a reviewer
accepts, and the next build to show whether it worked.

---

## K0 — Decisions (recommended first; each changes the plan below)

1. **The lever is a paragraph attribute in the override log, not a DesignSpec patch.**
   `AGENT_DESIGN.md` §1.3 says "DesignSpec patches, scoped to a spread". That has no
   renderer. Every `designspec/1` field is book-wide, and CSS paged media cannot select
   "the paragraph that lands on page 143": `@page :nth()` styles the page box, not the
   text flowing through it. A DesignSpec change reflows the whole book to fix one page.
   A typesetter fixes a widow by tracking one paragraph tighter or looser so it gains or
   loses a line. That is a node, and nodes already have a fix path: `set_attr` applies any
   attribute the node's type declares in `ast.schema.json` (`override_ops._t_set_attr`), and
   the override log already carries it through review, rebase on re-ingest, `resolve` and
   the worker. The attribute changes no text, so the integrity gate is unaffected.
   *Cost compared with what was said on 2026-10-12:* no migration (`override_ops` exists), no
   worker change (`worker._override_log_for` already supplies the log), no new route (the
   accept route is extended).
2. **The adjustment is chosen by a fixed rule, not by the model.** The model stays the
   verifier it is today. A confirmed widow or runt on a paragraph gets the next tracking step
   from a table, the same doctrine as `structure-propose`: the same verdicts always give the
   same proposals. The model never picks a value, and its output reaches the book only
   through a reviewer's accept (D9).
3. **Phase one covers widows and runts only.** `_measure_pages` knows which paragraph
   caused each (K1 records it). Hyphen stacks and rivers come from `pdftotext` word boxes,
   which name no paragraph. They stay verdict-only until something maps a baseline to a
   paragraph.

---

## K1 — Addressability: which paragraph made the defect

A proposal must name a node (`WIRING_PLAN.md` rule 2, addressability before consumption).
`pagemap/1`'s `paraRanges[].paraIndex` counts rendered elements in render order. It is not
an AST address.

- `rendering.ast_to_html` writes `data-ref="<docxId>"` on each `<p>` whose paragraph has
  one. Real DOCX ingest gives every paragraph a `docxId`. The six synthetic corpus books
  have none, so their HTML and pagemaps keep today's shape (the nightly
  reproducibility run is unaffected).
- `paginate._measure_pages` reads the attribute from the box's element. Each `paraRanges`
  item gains an optional `ref`, and each page gains optional `widowRefs` / `runtRefs` naming the
  paragraphs behind its flags. *As built:* the print renderer only (`PrintNotes`); EPUB
  sections carry no `data-ref`.
- `pagemap/1` schema: the new optional fields; codegen; `.gen.*` committed. `platform/pagescan`
  reads pagemap field names, but serde ignores fields it does not declare, so the crate
  needed no change (*as built*).
- Bumps: `extract` and `paginate` (they share the renderer). `ast-assemble` compares text only,
  so an attribute is safe. Every cached HTML and pagemap misses once.
- **Test:** a weasyprint render of a fixture that widows; the page's `widowRef` is the AST
  paragraph whose last line it holds.

## K2 — The lever: a paragraph's `fit`

- `ast.schema.json` paragraph `attrs` gains `fit`, a closed enum `tighter | tight | loose |
  looser` (no attribute = as designed). Being declared is what makes `set_attr` accept it.
- CSS path: class `fit-<value>` → `letter-spacing` in `rendering.emit_css`. The four
  values are house constants next to the existing print CSS (−0.02, −0.01, +0.01, +0.02 em,
  to be tuned on the real book).
- Typst path: the Lua filter maps the same class to `#text(tracking: …)`. It is honoured,
  not dropped. Read `typst_stages.py`'s docstring before touching it.
- EPUB: ignored on purpose, since a reflowing book has no fixed line to save. Said in
  `ast_to_epub_sections`.
- **The test that matters:** a real render where `fit: tight` on the widowed paragraph
  removes the widow line. Without it the lever is decorative.
- *As built:* pandoc's HTML reader drops a `<p>`'s classes, so the value rides on a
  `<span class="fit-<value>">` around the paragraph's text (print only), which both paths
  read: `emit_css` tracks the class, the Lua filter (`FOOTNOTE_FILTER_LUA`) turns the span
  into `#text(tracking:)[…]`. One table, `rendering.FIT_TRACKING`, feeds both; a test pins
  its keys to the schema enum. The real-render test sweeps paragraph lengths with the K1
  measurement page rather than needing a Word-corpus fixture: the lever needs no `docxId`,
  only K3's proposal does. On that page `tight` clears some widows, `tighter` more, `loose`
  none. Bumps: `extract`, `paginate`, `design-compile`, `design-compile-typst`, `paginate-typst`.

## K3 — Proposals in `layout-review/1`

- Each finding gains an optional `proposal {id: "pr-…", type: "fit_paragraph", docxId,
  value}`, closed enums, so `lint_schemas.py` still passes. No rationale string is stored;
  the API derives one from the kind and page.
- Rule: a `confirmed` widow or runt with a ref gets `tight`. If the paragraph is already
  `tight` it gets `tighter`; at `tighter` it gets nothing and is left to a human.
  Loosening is the follow-on if tightening proves the weaker fix on the real book.
- It cannot be a second `agent-proposal/1`: the DAG allows one producer per schema ID, the
  mistake `raw-pdf/1` once made.
- `layout-review` now also takes `doc-effective/1`, to read a paragraph's current `fit`. It
  stays terminal. Version bump.
- *As built:* `proposals` is an array, not one `proposal`: a page can hold several runts,
  and dropping all but one would hide fixes. Ids are `pr-` + 24 hex of
  sha256(docxId|fit_paragraph|value), the `structure-propose` scheme, so the same advice is
  the same proposal on every rebuild and the `tighter` step is a new one. A ref the
  document no longer has, or a paragraph a human set loose, gets no proposal.
  `tools/lint_schemas.py` allowlists `id` and `docxId`: our code writes both, never the
  model.

## K4 — The accept path

- `packages/api/src/routes/manuscripts.ts`: the structure view and the accept route read
  `layout-review/1` beside `agent-proposal/1` from the same build (`reviewedBuildArtifacts`).
  `proposalOp` finds the proposal in either.
- `contract.ts` ↔ `propose_stage.py`: `PROPOSAL_OPS.fit_paragraph = "set_attr"`,
  `PROPOSAL_PARAMS.fit_paragraph = ["value"]`, and a new `PROPOSAL_PATHS.fit_paragraph =
  "/attrs/fit"`. The overrides schema requires a `path` on `set_attr` and `proposalOp`
  copies only `from`/`to`/`value`, so without a fixed path the op is refused on its way to
  the log. All three pinned by `tests/test_single_source.py`.
- Review UI: the proposal shows its page and kind. Vitest, plus the Postgres integration
  suite for the accept.
- Revert already exists: a `set_attr` with no `value` removes the attribute, through the
  existing `PATCH /v1/documents/:id/overrides`.
- *As built:* `manuscripts.ts` `layoutProposals` flattens layout-review/1's findings into
  agent-proposal/1's shape (`sourceRef`, a rationale derived from kind and page, plus
  `page`/`kind`), deduped by id, since one paragraph can be behind a widow and a runt. Both
  routes read the two artifacts through one `reviewedProposals`; `null` only when neither
  producer ran. `ProposalReview` gains `page` and `kind`, and the panel shows them. The
  Python tables live in `stages/propose_stage.py` beside structure-propose's types, so the
  existing pins cover `fit_paragraph`; a new pin covers `PROPOSAL_PATHS`.
  `tests/integration/test_layout_proposals.py` accepts one against real Postgres (CI: the
  local integration database is not this project's).

## K5 — Verify: the next build is the re-render

`render_range` stays unavailable: a new layout is a build. After an accept, the next build
runs `resolve → paginate → preflight → layout-review`, and `layout-review` reports per
accepted `fit` op a closed outcome: `cleared`, `still-defective` or `not-flagged` (the
paragraph moved off any warned page).

The regression floor is preflight, not the raster diff (a corpus gate, not a per-build one).
An adjustment that strands an orphan fails the build outright. A new widow or runt on another
page is a new warning, which the same loop then reviews.

*As built:* the outcomes are `cleared`, `still-defective` and `unmeasured`, not
`not-flagged`. One stage run sees only its own build, so "was flagged before, is not now"
cannot be told apart from "was never flagged"; what this build CAN say is whether the
pagemap still names the paragraph behind a widow or runt (`still-defective`), names it on its
pages and behind nothing (`cleared`), or never names it (`unmeasured`: the Typst path records
no refs, K1, and calling those cleared would be the unmeasured-is-clean mistake). The report
is `fitOutcomes`, one entry per paragraph carrying a `fit`, written whether or not anything
warned: a fix that worked removes the warning, and that build is the one a reviewer needs.
A still-defective paragraph that is confirmed again gets K3's next step. layout-review v3.
Reviewers see them (2026-10-14): `GET /v1/manuscripts/:id/structure` returns the reviewed
build's `fitOutcomes`, passed through and never recomputed (`null` when layout-review did
not run, or no row was readable: not "no fits"), and the review panel lists each in words,
`unmeasured` never as cleared, plus the fits logged since that build, which await the next.

---

## Order and gates

One green commit per phase: K1 → K2 → K3 → K4 → K5. Each runs the full suite, the lints and
`python platform/stages/integrity.py`. K1 also runs `cargo test`, and K4 vitest and the
integration suite.

**Live check (2026-10-15): the house design leaves the loop nothing to catch.** The real book
measures 0 widows and 0 runts, and that is not luck: the house stylesheet sets `widows: 2;
orphans: 2` on every paragraph and `.keep` holds each one's last two words together.
Searched for a construct that beats them, in GFS Didot (the house face) and DejaVu Serif
(CI's default serif), over ~1,100 pages: short 2-3 line paragraphs, paragraphs before a
heading or scene break, beside a table, every paragraph role, left/centre alignment, a
paragraph ending in emphasis or a link, footnotes and long footnotes, one chapter and
eight. None widowed or ran short. With the same rules removed the same books show 27
widowed and 66 runt paragraphs, so the measurement sees them. (The search also found
that a `chapter-opening` paragraph anywhere but a chapter's start crashes weasyprint 70
in its drop-cap float -- ingest never sets that role; filed separately.)

So the loop is a safety net on the CSS path: for what the stylesheet cannot prevent, and
for a design that relaxes it. Its mechanics are proven end to end instead
(`stages/tests/test_fit_loop.py`): `corpus/word/word-composition.docx`, written by Word,
goes through the real ingest, integrity gate, resolve, paginate, preflight composition
check and layout-review, under the house stylesheet with `widows: 2` relaxed to 1 and
nothing else changed. The Compositor's model is the one stand-in, confirming every widow.
Each build's proposals are accepted into the log as the API would log them, and the next
build must report every accepted fit, against the value it applied, as `cleared` or
`still-defective`, without changing a word. At least one must clear within three builds.
Measured: 6 widowed paragraphs in GFS Didot and 7 in DejaVu Serif; `tight` cleared 1 and 2
of them, `tighter` 4 of 5 and 3 of 5, and on each face the second reflow widowed one new
paragraph downstream -- the next build's proposal. With the lever disabled (every fit tracking 0),
the test fails. A live run of the real Compositor adds only the model's verdicts.

## Non-goals

- No DesignSpec change from an agent. Book-wide design stays a human choice.
- No model-chosen values. No private rendering.
- Hyphen stacks and rivers get no proposals (K0.3).
- Neither hard gate is touched. Nothing consumes `layout-review/1`.
