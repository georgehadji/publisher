# Structure repair plan — misfiled sections and block-level verdicts

**Status:** B0–B2 done (2026-10-07). B3–B9 open. Planned 2026-10-07 against `fac9254`.
**Scope:** the two items `REMAINING_WORK.md` §1.3 still lists as open:

1. a front/back-matter section the classifier reads as belonging somewhere else can only
   be flagged, not fixed;
2. `split_chapter`, `reclassify` and promote proposals are never produced, because
   classification stops at chapters and sections.

Identifiers use the prefix **B** (B0–B9) so they don't collide with the D/E/U/A/G/S/O/P/T/R
schemes `tools/lint_docs_claims.py` checks, or with `WIRING_PLAN.md`'s W.

---

## 0. What is actually broken

Reading the code changed what both items mean. The findings first, because the phases
follow from them.

**F1. `end_body` (shipped in `fac9254`) can never be proposed from a real build.**
- Ingest scores every back-matter section `BACK_MATTER_BY_PATTERN = 0.9`
  (`docx_to_ast.py:138`), and `structure-infer` only sends nodes below
  `ESCALATE_BELOW = 0.8`. So no back-matter section ever reaches the classifier.
- The real misfiling looks different from what `end_body` assumes. Once a section's title
  matches a back-matter pattern, ingest folds every later chapter into that one section,
  with its title kept as a plain paragraph (`docx_to_ast.py:708–715`). This is deliberate:
  it stops bibliography entries styled as headings from opening chapters.
- So a chapter whose title merely looks like back matter ("Notes on a Scandal") swallows
  the rest of the book. `end_body` on that section yields one chapter holding all of them.
  Recovering them takes `end_body` followed by a `split` at each folded title.

**F2. "Wrong end" is mostly "wrong type".**
- Front-matter sections are positional: everything before the body boundary.
- Ingest types them only as `titlePage` or `toc` (`FRONT_MATTER_TYPE`, `docx_to_ast.py:72`).
- So a front section the model reads as `front-dedication` is a retype, not a move. No op
  can retype a section today: `reclassify` validates its result through `_valid_node`, which
  needs a `$defs` entry per type, and section types have none (`override_ops.py:141`).
- A front section read as `back-also-by` or `back-about-author` is usually right where it
  belongs. An "Also by" card page conventionally sits in the front matter. The schema
  forbids both types there (`frontMatterNode`'s section enum stops at `prologue`).

**F3. A true cross-end misfiling is a reorder.** An epilogue that really sits before the
body is the author's order in the DOCX. Moving it changes the order of the book's words
after the integrity gate has certified them. Every op today preserves that order, and the
op tests assert it (`_text_in_order`).

**F4. Block-level verdicts need four things the pipeline lacks:**
- **Confidence on blocks.** Only `chapter` and the section nodes carry `confidence`;
  `heading` and `paragraph` don't.
- **Blocks in the classifier's input.** `structure-infer` sends chapters and sections only
  (`_doubtful_nodes`), in one call under a 60s timeout.
- **Parameters on accepted proposals.** The accept route copies only `id`, `sourceRef`, the
  op and `rationale` (`manuscripts.ts:267`). A `reclassify` needs `from`/`to`, which
  `agent-proposal/1` already carries but the route drops.
- **A mapping that can express direction.** `PROPOSAL_OPS` maps each proposal type to
  exactly one op. A heading verdict can need `promote` or `demote`.

**F5. Neither boundary op can be logged by hand.** The review panel has buttons for
`split`, `promote`, `demote`, `insert`, `delete` and `resolve_ambiguity`. It has none for
`start_body` or `end_body`, so today they are reachable only through the raw PATCH route.

---

## 1. The constraints that decide the shape

1. **D9: model output reaches a book only as an op a person accepts.** Every fix here is a
   proposal and an op. No stage applies a verdict itself.
2. **The text-order invariant.** After `ast-assemble`'s integrity gate, an op may move a
   boundary or change a node's type. It may not drop, add or reorder words. That rules out
   F3's move (declined, §4) and any reclassify that drops text, such as a paragraph becoming
   a `sceneBreak`.
3. **Closed vocabularies, never model text.** Every proposal parameter comes from a closed
   table keyed on the model's closed-enum label (`LLM_STRATEGY.md` §3). A label maps to a
   type; nothing the model writes reaches `from`, `to` or `value`.
4. **One source per fact, pinned by a test.** Op and proposal enums live in the schemas;
   `contract.ts` and the PATCH route restate them; `tests/test_single_source.py` pins each
   copy. Every new enum value lands in all copies, and the regenerated `.gen.*` files land
   in the same commit.
5. **Absent means "not measured".** A new `confidence` field is optional, and a block with
   none is neither sent as doubtful nor counted as certain. That is how chapters work today.
6. **The DAG is declared.** A stage's input change is a declaration change, with a
   `@stage(version=)` bump; `platform/stages/integrity.py` proves the graph still closes.

---

## 2. Paradigms and patterns, matched to code that already exists

| Pattern | Where it already lives | How this plan uses it |
|---|---|---|
| Functional core, imperative shell | Ops are pure `AST → AST` functions; stages do the I/O | Every new op and decision table is a pure function. Only the stage bodies read or write CAS. |
| Table-driven dispatch | `_TRANSFORMS`, `SECTION_LABELS`, `PROPOSAL_OPS` | Both decision tables (sections, blocks) are data: `(node kind, label) → proposal`. No if-chains that grow per case. |
| Command log (event sourcing) | The override log, replayed by `resolve`; ids derived with `_derived_id` | Unchanged. Undo stays "drop the op". Dependent fixes become later commands in the same log (B0). |
| Schema-derived single source | `_section_types()`, `classify_contract.labels()` | Allowed retypes are read from the AST schema's unions, never hand-listed. |
| Totality by exhaustive tests | `test_every_proposal_type_maps_to_an_implemented_op` | Each table is tested over the *whole* label set. A new label without a row fails CI. |

Deliberately not used: class hierarchies, visitors, or a plugin registry. The AST is
plain dicts and the codebase dispatches through function tables; a second style would be
two ways to read one module.

---

## 3. Phases

Each phase is one commit that leaves every gate green. Gates in every phase: the targeted
tests via `./scripts/test.ps1 -k`, the full suite, the CI lints listed in `CLAUDE.md`, API
`tsc` + vitest, web `tsc`, and the codegen replay
(`cd schemas && node codegen/generate.mjs && git diff --exit-code`).

### B0 — classify the effective document, not the raw AST

*Closes:* the ordering problem behind F1. Also makes every later dependent fix possible.

- `structure-infer` and `structure-propose` take `doc-effective/1` (from `resolve`) instead
  of `ast/1`. That changes only the input declarations, with a version bump each.
- **Why:** a fix often needs two steps. F1's recovery is `end_body`, then a `split` per
  folded title. A retype of a section that `start_body` just moved targets a node that only
  exists after it. Against `doc-effective/1`, each rebuild proposes against the book as
  already corrected, so the next step appears once the previous one is accepted.
- **What stays the same:**
  - `resolve` replays the log in acceptance order, and an op may already target a node an
    earlier op created (`test_an_op_may_aim_at_a_node_an_earlier_op_created...`).
  - Proposal ids are stable per node and kind, so accepted ones stay filtered.
  - Answers are cached on the nodes sent, so a rebuild re-buys only what changed.
- **Rejected alternative:** a compound proposal (one accept, several ops). It needs a
  multi-op accept, partial-failure semantics and a schema change, and it shows the reviewer
  one decision that is really several.
- **Gates:**
  - DAG integrity.
  - Reachability: still exactly when `api_key` is supplied.
  - A new stage test: accept a `start_body` proposal, run `resolve`, propose again. The
    second run proposes against the moved chapters, and the accepted proposal is gone.

### B1 — make `end_body` reachable: score the fold

*Closes:* F1.

- When a back-matter section absorbs at least one later heading candidate
  (`docx_to_ast.py:708–715`), ingest scores it `BACK_MATTER_ABSORBED = 0.5` instead of
  `0.9`. The pattern match itself was confident; what's in doubt is everything it swallowed.
- A section that absorbed nothing keeps 0.9, so the bibliography case that motivated the
  fold doesn't get resent.
- **Gates:**
  - Ingest test: a DOCX with "Notes on a Scandal" mid-book scores the section 0.5.
  - A real bibliography with heading-styled entries still scores 0.9, since those entries
    aren't chapter-strength candidates. Define "absorbed" by the absorbed block's
    `heading_confidence`, so the two cases separate on evidence, not on the title string.
  - The `structure-propose` table test gets a row: section read as `chapter-title` →
    `end_body`.
  - The folded titles become `split` proposals in B8.

### B2 — accepted proposals carry their parameters

*Closes:* F4's third gap. This channel has to exist before any parametrised proposal.

- The accept route copies `from`, `to` and `value` from the proposal into the op, but only
  the parameters its type declares.
- `PROPOSAL_OPS` keeps its strict `type → op` shape, so the existing single-source regex
  and `satisfies Record<string, OverrideOp["op"]>` still hold. A sibling table,
  `PROPOSAL_PARAMS: type → allowed parameter names`, is defined in `propose_stage.py`,
  restated in `contract.ts`, and pinned by `test_single_source.py` the same way.
- A proposal carrying a parameter its type doesn't declare is refused with 422, not
  silently trimmed.
- Direction (promote vs demote) is handled by giving each direction its own proposal type
  (B8), not by a parameter. One type, one op keeps the table total and readable.
- **Gates:**
  - Vitest: a `reclassify` proposal is accepted as an op with `from`/`to`; an undeclared
    parameter gets 422.
  - Single-source test for the new table.

### B3 — `reclassify` acts on sections, and on heading levels

*Closes:* the op side of F2.

- `reclassify` accepts a section whose `from` and `to` are both section types valid in the
  same list.
- The result is validated against that list's union (`#/$defs/frontMatterNode` or
  `backMatterNode`), not against a per-type `$defs` entry.
- Content, `sourceRef` and `confidence` are kept.
- A retype across ends (a back type inside `frontMatter`) is refused by that same schema
  check, with the schema's own message.
- `reclassify` to `heading` takes an optional integer `value` as the level, so a paragraph
  read as `heading-2` becomes one op instead of a reclassify plus a demote. The overrides
  schema gets a per-op `allOf` constraint: `value` must be an integer from 1 to 6 when
  `to` is `heading`.
- **Gates:**
  - Op tests: a front retype applies; a cross-end retype is refused; the result is a
    valid AST; `_text_in_order` is unchanged.
  - The override-schema edit is regenerated and committed.

### B4 — types valid at either end

*Closes:* F2's second half.

- Add `alsoBy` and `aboutTheAuthor` to `frontMatterNode`'s section enum.
- The change is additive, so it stays `ast/1`: every existing document still validates.
- The renderers already handle both types at any position (`rendering.py:612`, `:682`;
  EPUB uses the same renderer). A render test proves it for a front-matter `alsoBy`.
- `SECTION_LABELS` stays one mapping. The `back-` in `back-also-by` names a label, not a
  position rule; document that next to the table.
- Ingest doesn't change. It never types front matter beyond `titlePage`/`toc`, and B5
  retypes it on evidence.
- **Gates:**
  - Schema test for a front `alsoBy`.
  - Render test.
  - Codegen committed.

### B5 — section decision table: retype proposals

*Closes:* the proposal side of F2.

- `structure-propose`'s section branch becomes a table keyed on
  `(end, label → target type)`:

| Section verdict | Proposal | Op |
|---|---|---|
| its own type's label | none (agrees) | — |
| `chapter-title`, earliest front section | `start_body` | `start_body` |
| `chapter-title`, last back section | `end_body` | `end_body` |
| `chapter-title`, any other section | none (moves with the boundary) | — |
| a section label whose type is valid at this end | `reclassify` | `reclassify` (`from`, `to`) |
| a section label whose type is not valid at this end | `flag_ambiguity` | `flag_ambiguity` |
| anything else | `flag_ambiguity` | `flag_ambiguity` |

- The target type is the inverse of `SECTION_LABELS`; "valid at this end" is
  `_section_types()` split per union. Neither is hand-written a second time.
- **Kept on purpose:** ingest's documented decision not to score the front-matter subtype
  (`docx_to_ast.py:664–667`). Front sections are still sent only on the 0.5 fallback, so
  retype proposals arise on fallback builds. Overturning that would flag every title page
  of every book. Revisit if review data shows real retypes going unasked.
- **Gates:**
  - Exhaustive table test: every label in `classify_contract.labels()` × {front, back}
    yields exactly one row.
  - Every produced proposal applies to the document it was made from.

### B6 — blocks carry confidence

*Closes:* F4's first gap. Schema and ingest only; nothing reads it yet.

- Optional `confidence` (0–1) is added to the `heading` and `paragraph` defs, with the same
  semantics as on chapters: absent means not measured.
- Ingest scores three cases, as named constants beside the existing ones (`docx_to_ast.py`,
  lines 132–138):
  - **In-chapter headings.** `_heading_depth` already computes a confidence for bold
    headings below level 1 and discards it (`docx_to_ast.py:337–343`). Keep it.
  - **Folded titles** inside an absorbing back-matter section (B1): the title paragraph
    gets `FOLDED_HEADING = 0.5`.
  - **Title-like paragraphs** inside a chapter: short, standalone, matching a
    chapter-number pattern, but neither styled nor upper-case. Score them
    `TITLE_LIKE_PARAGRAPH = 0.5`.
  - **Where the patterns come from.** The chapter-title patterns already exist in
    `rules.py:273`, but `.importlinter` keeps services independent, so `publisher_ingest`
    can't import `publisher_structure`. Restate the list in ingest and pin the two copies
    together in `tests/test_single_source.py`, the way that file already pins every other
    unavoidable copy. Don't write a second, different detector.
- `ingest` gets a version bump.
- **Gates:**
  - Ingest tests per case.
  - The schema pin test.
  - A candidate-count check over `corpus/manuscripts/` and the local real manuscript,
    recorded in the commit message. The volume decides B7's batch size, so measure it
    before choosing one.

### B7 — `structure-infer` sends doubtful blocks, in batches

*Closes:* F4's second gap.

- Doubtful blocks join the input. Each is sent as
  `{sourceRef, current, text, context}`, the same shape chapters use:
  - `current` is `heading-<level>` or `paragraph`;
  - `context` is the following words, as today.
- **Prompt and cache:** the input shape and prompt are unchanged, so `prompt_version`
  stays `1.1` and cached answers stay valid. If evaluation shows the model needs the
  preceding words, that is a `prompt_version` bump, which is part of the cache key.
- **Batching:**
  - Nodes are grouped by chapter, in book order, and packed into batches of at most
    `MAX_NODES_PER_CALL`. A chapter isn't split unless it alone exceeds the limit.
  - Each batch is its own gateway call and cache entry. Because batches are
    chapter-aligned, an edit in one chapter re-buys only that chapter's batch.
- **Cap:** `MAX_NODES_PER_BUILD` bounds cost. Beyond it, the remaining nodes aren't sent,
  and the stage emits a `classification-truncated` warning with the count, through the
  existing warnings channel. It is never silent.
- `timeout_s` becomes per-batch × batch count, bounded.
- **Gates:**
  - Stage tests with a counting provider: batch boundaries are deterministic; an edit in
    chapter k re-calls only batch k; the cap warns with the right count.
  - The cost estimate is recorded next to the route in `policy.yaml`.

### B8 — block decision table: split, promote, demote, reclassify

*Closes:* item 2.

- One new proposal type, `promote_heading → promote`, joins the agent-proposal enum,
  `PROPOSAL_OPS`, `contract.ts` and the route.
- The table:

| Block | Verdict | Proposal | Op |
|---|---|---|---|
| paragraph or heading inside a chapter, not its first block | `chapter-title` | `split_chapter` | `split` |
| heading level n | `heading-m`, m < n | `promote_heading` | `promote` (one level; B0 surfaces the next) |
| heading level n | `heading-m`, m > n | `adjust_heading_level` | `demote` |
| paragraph | `heading-m` | `reclassify` | `reclassify` (`to: heading`, `value: m`) |
| heading | `paragraph` / `first-paragraph` / `chapter-opening` | `reclassify` | `reclassify` (`to: paragraph`) |
| paragraph | `blockquote` / `epigraph` / `dialogue` / `sidebar` | `reclassify` | `reclassify` (wrapper; `_reshape` already handles these) |
| paragraph | `verse` | `flag_ambiguity` | — (`_reshape` has no verse case; add one, then promote this row) |
| any | `scene-break` / `dinkus` / `page-break` / `section-break` | `flag_ambiguity` | — (the op would drop the block's words) |
| any | front-/back-matter labels | `flag_ambiguity` | — (a body block read as matter; no op fits) |
| any | every other label, `uncertain` | `flag_ambiguity` | — |
| any | agrees with `current` | none | — |

- F1 closes here end to end. The folded titles that B6 scores are `chapter-title` verdicts
  on paragraphs. Once the reviewer accepts `end_body` (B1), the next build (B0) proposes a
  `split` at each one.
- **Gates:**
  - Exhaustive table test over `labels()` × {paragraph, heading levels 1–6, first block
    of a chapter}.
  - Every row's proposal, accepted as the API builds it, applies to a synthetic node and
    leaves `_text_in_order` unchanged.
  - Single-source tests for the new type.

### B9 — the review panel can log every boundary and retype by hand

*Closes:* F5, and gives a person the same reach the proposals have.

- **Front-matter sections:** "The body starts here" (`start_body`) and "Retype as…", a
  closed `<select>` built from the types valid at that end.
- **Back-matter sections:** "The body ends here" (`end_body`) and the same retype select.
- Both go through `FORM_OPS` in `packages/web/src/review/actions.ts`. The select's options
  come from the API contract, not a web-side list.
- Block proposals need no UI change: the panel renders proposals generically.
- **Gates:**
  - `actions.test.ts` rows for the three ops.
  - Web `tsc`.
  - A Playwright run of the structure view against the compose stack, if it is up.

---

## 4. Declined, with the condition that would reopen each

| Declined | Why | Reopen when |
|---|---|---|
| A `move_section` op, carrying a section to the other end | It reorders words after the integrity gate. Every case ingest actually produces is a retype (B3–B5) or a boundary (B0/B1). | A real manuscript whose DOCX order is itself wrong, and a publisher who wants the move. Design it then as an explicit order change: same word multiset, the move listed in the build report. |
| Compound proposals | B0's rounds give the same result through ops that already exist. | Review data shows reviewers abandoning multi-round fixes. |
| Model-suggested titles (`suggest_title`) | Free text into the book (§1, constraint 3). | Never, under D9 as written. |
| Property-based testing (Hypothesis) | The domain is finite (labels × node kinds); exhaustive parametrisation covers it. Adding the dependency means a hashed lock entry. | An op gains an unbounded input. |
| Scoring every front-matter subtype | Ingest's documented choice: flagging the lump buries real questions. | Retypes going unasked shows up in review data. |

---

## 5. Risks and costs

- **Latency.** B0 makes `structure-infer` wait for `resolve`. `resolve` is a pure replay
  over the AST, so the cost is small; measure it in the B0 commit.
- **Spend.**
  - B7 multiplies the nodes sent. At the route's `cost_per_call` of $0.003, a book with
    200 doubtful blocks in batches of 40 costs about $0.015 per cold build, and almost
    nothing on a cached rebuild.
  - `MAX_NODES_PER_BUILD` is the backstop.
  - B6's candidate counts are the real input to both numbers.
- **False positives.** Title-like paragraph detection (B6) is the noisiest signal. It only
  produces proposals, never changes, and B6 measures its volume before B7 sends anything.
- **Manuscript text as prompt injection.** The reply is closed-enum and checked against the
  nodes sent (`classification_document`). Every parameter comes from tables keyed on that
  enum. Manuscript text can make the model pick a wrong label, which a person then sees,
  but it can't make the pipeline emit an op outside the tables.

---

## 6. Order at a glance

| Phase | Depends on | Touches |
|---|---|---|
| B0 | — | `structure_infer_stage.py`, `propose_stage.py` declarations |
| B1 | — | `docx_to_ast.py`, `ingest_stage.py` |
| B2 | — | `manuscripts.ts`, `contract.ts`, `propose_stage.py`, `test_single_source.py` |
| B3 | — | `override_ops.py`, `overrides.schema.json` |
| B4 | — | `ast.schema.json`, `rendering.py` test |
| B5 | B2, B3, B4 | `propose_stage.py` |
| B6 | — | `ast.schema.json`, `docx_to_ast.py`, `ingest_stage.py` |
| B7 | B6 | `structure_infer_stage.py`, `policy.yaml` |
| B8 | B0, B2, B3, B7 | `propose_stage.py`, `agent-proposal.schema.json`, `contract.ts`, route |
| B9 | B3, B4 | `packages/web/src/review/` |

B0–B4 and B6 are independent and can land in any order. The sequence above puts the
shipped gap (F1) and the channels (B0, B2) first, so no proposal type exists before
something can accept it.
