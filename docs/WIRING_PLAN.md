# Wiring plan — every open item in `REMAINING_WORK.md`, in build order

**Status:** W0–W10 done (2026-10-02). W11 stays blocked by design.
Planned 2026-10-01 against `ae1a05e` plus the uncommitted split/merge work.
**Scope:** every item `docs/REMAINING_WORK.md` still lists as open. Each phase below says
which item it closes, what changes, which gates prove it, and why it sits where it does.

Identifiers use the prefix **W** (W0–W11) so they don't collide with the D/E/U/A/S/O/P/T/R
schemes `tools/lint_docs_claims.py` checks.

---

## How the order was chosen

Five rules from the architecture decide the order. Each phase is one commit that leaves
every gate green.

1. **Build the channel before the producer.** A report nobody reads is the "measured and
   discarded" defect (`REMAINING_WORK.md`, shape 2). `StageResult.warnings` is read by
   nothing today, so the resolve warning for an inapplicable op, added with split/merge,
   goes nowhere in a worker build. The channel comes first (W0), because almost every
   later phase emits a non-fatal report.
2. **Addressability before consumption.** A proposal is useless unless it can name the
   node it's about. `structure-infer` currently labels nodes `block-<i>`, which no op can
   target. Ids come first (W3), then the proposals built on them (W4).
3. **Every proposal must land as an implemented op.** Agent and LLM output reaches a book
   only as override ops a human accepts (D9, `AGENT_DESIGN.md`). So the op set must be
   complete (W1) before the proposal channel (W4). Otherwise accepting a proposal would log
   an op that `resolve` refuses.
4. **Harden a write path before adding a second one.** The review UI writes as one shared
   actor. The proposal-accept endpoint (W4) is a second writer, so reviewer identity (W2)
   lands first and the new path starts out authenticated.
5. **Honesty before new capability.** A check that silently reports "clean" (pagescan's
   river detector, `CveGate`) is worse than a missing one. Those are fixed without waiting
   on the capability they lack (W7).

Domain phases (W6 prepress, W8 fonts, W9 ingest) don't depend on the loop and could run in
parallel. They're ordered by how often each makes a vendor reject a book. CI gates (W10)
come after the checks they run. W11 lists what's blocked on something outside the code.

```
W0 diagnostics ─┬─ W1 override ops ─ W2 reviewer identity ─┐
                │                                          ├─ W4 proposals ─ W5 agent runtime
                └─ W3 addressable inference ───────────────┘
W6 prepress · W7 honesty · W8 fonts · W9 ingest      (independent; after W0)
W10 CI gates                                         (after W6–W9)
W11 blocked                                          (needs a decision or an outside input)
```

---

## W0 — Stage diagnostics reach the reviewer

**Closes:** §1.1 "a non-fatal report has nowhere to go".

- `platform/db/migrations/007_stage_warnings.sql`: `build_stages.warnings JSONB`. The worker
  role already writes `build_stages`.
- `worker.py` `_record_stage` stores `result.warnings` (code, severity, message,
  suggested fix, sourceRef) beside `metrics`. The failure path already records artifacts,
  and now records warnings too.
- The Temporal activity returns warnings in its result dict.
- `tracer_bullet.py` prints every stage's warnings, not just preflight's.
- `GET /v1/builds/:id` returns each stage's `metrics` and `warnings`. It also exposes the
  `StageWarning` type in `packages/api/src/contract.ts`.
- **Tests:** worker integration (a resolve warning round-trips to the row); API route test;
  an executor unit test proving a warning survives to the result.

## W1 — The override layer is complete

**Closes:** §2.3, plus the §1.1 "matched but can't act" silence.

- **Matched but can't act is reported, not silent.** Examples:
  - a `retitle` aimed at a node with no title slot;
  - a `reclassify` whose `from` no longer matches the node's type (a stale op).

  Both raise `InapplicableOverride`, which reaches the reviewer through W0.
- **`promote` / `demote`** (headings ↔ chapters):
  - A level-1 heading directly in a chapter is promoted into a chapter, like `split`, and
    titled with the heading's text.
  - A heading at level n > 1 is promoted to level n−1.
  - A chapter is demoted into the chapter before it, like `merge`, and its title becomes a
    level-1 heading, not a paragraph.
  - A heading at level n < 6 is demoted to level n+1.
  - Promote and demote are inverses. Tests check the round trip and that no word is lost.
- **`insert`** adds a structural node after the target block. `value` is `sceneBreak` or
  `pageBreak`, a closed set with no text. Inserted author text would bypass the integrity
  gate, which runs before `resolve`.
- **`set_attr`**: `path` names one attribute, and `value` is checked against **that node
  type's attribute definition in `ast.schema.json`** (read from the schema, not copied).
  Attributes other ops own are refused, and so are derived ones: `title` (retitle), `id`
  and `number`.
- **`resolve_ambiguity`** clears the target's `flag_ambiguity` flags. If `value` names one
  flag op's id, it clears only that flag.
- **`rename` is removed from `overrides/1`'s enum.** It had no meaning distinct from
  `retitle`, and the API has refused it with a 422 since the log existed, so no stored log
  can contain one. Its removal is recorded in the schema description.
- **API:** `APPLICABLE_OPS` covers every op, and the partition test becomes "implemented ==
  schema enum". The structure view lists each chapter's blocks (docxId, type, heading level,
  an excerpt of 80 characters at most), so a split has something to pick.
- **UI:** each chapter expands to its blocks. A block gets "Start a chapter here" (split),
  "Promote/Demote" (headings) and "Scene break after" (insert). The action builds every op
  server-side, as today.
- `resolve` bump; codegen re-run; schema-parsing pins updated.

## W2 — Reviewers have identities

**Closes:** §1.1 "no reviewer authentication (local dev only)".

- **Decision taken (same mechanism the API already trusts):** each reviewer gets an argon2id
  token, `PUBLISHER_REVIEWER_TOKENS="<hash>:<tenant>:<reviewer>;…"`, hashed with the existing
  `npm run hash-token`.
  - `plugins.ts` resolves such a token to a principal `{kind:'reviewer', tenantId,
    reviewer}`.
  - `PATCH …/overrides` **sets `actor` from the principal** (`user:<reviewer>`) and refuses
    a body `actor` that differs. Today a caller can write any actor.
- **Web:**
  - A `/sign-in` page posts the token to a Server Action, which checks it against the API
    and stores it in an httpOnly, SameSite=Strict, `secure` cookie.
  - The review page and the Server Action forward the *reviewer's* token, not a shared one.
    The shared `PUBLISHER_API_TOKEN` is kept only for local dev, and only when no reviewer
    tokens are configured.
  - `middleware.ts` redirects unsigned requests to `/sign-in`.
- **Tests:** the app.test auth matrix gains the reviewer rows; an actor-spoofing test; the
  web action refuses without a session.

## W3 — Inference sees what ops can target

**Closes:** §3.5, and the id half of §1.3.

- `structure-infer` consumes **`ast/1`**, not `typescript-html/1`. It sends the nodes ingest
  scored below `ESCALATE_BELOW`, keyed by their `sourceRef.docxId`.
  - It stops re-running `rules.py` over HTML: that is a second, unscored classifier, and its
    `block-<i>` ids match nothing.
  - DAG: `structure-infer` now runs after `ast-assemble`, in parallel with `resolve`.
- `OpenRouterProvider` sends `response_format: {type: json_schema, strict: true}` built from
  `classification.schema.json`. A reply outside the closed enum can't come back, so it can't
  fail the parse at run time.
- Provider pinning and reasoning effort are read from `platform/routing/policy.yaml`'s route
  (`provider.order`, `allow_fallbacks: false`), the same rule `art_policy` enforces for
  images.
- `PromptCacheManager` returns the route's real system prompt and schema as a stable prefix,
  instead of a placeholder.
- **Tests:** a request-shape test (schema attached, provider pinned), ids that round-trip,
  and the DAG reachability test.

## W4 — LLM advice reaches the reviewer as proposals

**Closes:** the consumption half of §1.3.

- New stage **`structure-propose`**: `classification/1 + ast/1 → agent-proposal/1`
  (terminal).
  - A **deterministic** translation. The LLM's decision is already frozen in
    `classification/1`, and this stage adds no model call.
  - Each proposal maps one-to-one to an implemented op (W1):

    | Proposal | Op |
    |---|---|
    | `split_chapter` | split |
    | `merge_chapters` | merge |
    | `adjust_heading_level` | promote/demote |
    | `reclassify` | reclassify |
    | `flag_ambiguity` | flag_ambiguity |

  - It is reachable exactly when `structure-infer` is, so a build without an API key is
    unchanged.
- **API:**
  - The structure view returns the latest build's proposals that the log hasn't yet
    accepted. An accepted op's id is `ov-<proposal id>`, so "accepted" is a lookup, not a
    second table.
  - `POST /v1/manuscripts/:id/proposals/:pid/accept` builds the op **server-side** from the
    stored artifact, with the actor taken from the W2 principal, and appends it. The
    browser sends only the proposal id.
- **UI:** a proposals list with Accept on each.
- **D9 holds:** nothing LLM-derived reaches `resolve` unless a person accepts it.
- **As built:** only chapters are classified (W3), so three types are produced:
  `merge_chapters` -> merge, `adjust_heading_level` -> demote, `flag_ambiguity`. A
  chapter-level verdict has no block to split at, and promote/reclassify of a chapter mean
  nothing an op does. The accept route takes `{actor}` and refuses a reviewer naming anyone
  but themself, the same rule as the PATCH route.

## W5 — The agent runtime dispatches its tools

**Closes:** §1.2.

- `AgentRuntime` takes an injected `AgentProvider` (a Protocol), as `InferenceGateway` does
  since E6.1.
  - `execute` becomes a real loop: the model returns tool calls, `registry.call` runs them,
    and the results go back, until a final answer or the budget (`max_turns`, tokens, cost)
    runs out.
  - The final answer is validated against `agent-proposal/1`.
- The role-switch simulation (`_run_structure_wrangler` and friends) moves out of the
  production package into `services/agents/tests/`, like `fabricating_provider.py`.
- `OpenRouterAgentProvider` uses the chat tools API through the same egress proxy.
- **Wiring:** `structure-propose` gains an optional refinement. When the `api_key` root
  input is present, the Structure Wrangler reviews its proposals with the read-only tools
  (`query_nodes`, `sample_text`) and may drop or re-rank them. It may not add an op type.
  - The Compositor stays unwired (see W11): it needs rendered pages to verify a patch.
  - The Preflight Explainer stays a library call.
- **Tests:** a scripted provider drives a multi-turn loop that really dispatches tools; a
  budget stop; a schema-invalid final answer fails.
- **As built:** the caller supplies the answer schema, not always `agent-proposal/1`. The
  Wrangler's is a `keep` list whose items enumerate the ids it was given, which is
  stricter than "may not add an op type": it cannot edit a proposal either. The Compositor's
  is `agent-proposal/1`; the Explainer's is an explanations list. There is no `sample_text`
  tool: `query_nodes` over the bound AST covers it.

## W6 — Prepress checks vendors reject on

**Closes:** §2.2 spine vs stock, gutter/creep, and the "every vendor gets the default" note.

- **Stock caliper, stated where the vendor publishes it:**
  - KDP: white 0.0572 mm (0.002252 in), cream 0.0635 mm (0.0025 in) a page.
  - Profiles whose vendor doesn't publish a number keep the schema default, but the `cover`
    stage now **warns that the spine width uses a default caliper**. It stops presenting a
    guess as a measurement.
- **Gutter by page count:** a profile `bindingSpec.minGutterMm` table by page-count band.
  KDP publishes one: 0.375 in for 24–150 pages, rising to 0.875 in above 700.
  - Preflight fails an interior whose inside margin, read from the designspec in the
    `design-compile` output, is below the band for its page count.
  - Profiles without a table skip the check with "not stated".
- `maxOrphanPages` and `maxInkCoverage` stay schema defaults. That is a vendor fact nobody
  publishes; it's written down, not guessed.
- Preflight and cover bumps; tests per band edge.
- **As built:** the table is `bindingSpec.minInsideMarginMm` (`[{maxPages, mm}]`), and the
  margin is measured from the press PDF's word boxes (`pdftotext -bbox`), not read from the
  designspec. That checks what prints, and needs no new DAG edge into `preflight`.

## W7 — No check reports clean without looking

**Closes:** §1.4, §2.1 (the honest part), §3.2, and the font-manifest note in §3.1.

- **pagescan:** `detect_rivers` and `detect_hyphen_stacks` add an `unmeasured` entry to
  `ScanResult` instead of returning nothing, so a scan never reads as clean for a defect
  class it can't see.
- **Rust crates (decision):** keep them and pin them.
  - Remove the comment promising a `lib_py.rs` that was never written.
  - Add a cross-implementation test: fixed inputs whose digests (CAS) and defect lists
    (pagescan) are committed as JSON, checked by `cargo test` and by the Python and TS
    suites.
  - A PyO3 binding stays in `ARCHITECTURE_ROADMAP.md`.
  - `platform/cas/ts` is kept only if the agreement test covers it; nothing in `packages/`
    imports it.
- **`CveGate` deleted.** `pip-audit` and `npm audit` are the real gates; the CI comment and
  the module go.
- **Font manifest hashes the files.** `fontvault` hashes each face's bytes, not its metadata,
  so a changed font file changes the cache key.
- **As built:** the cache key part needed a hook. The executor passed an empty fontset
  hash to every key, and platform may not import fontvault. A stage now declares
  `@stage(cache_salt=fn)`, a function of its inputs that the executor mixes into the
  key; `paginate`'s is the hash of the font files its CSS resolves to. The pagescan
  agreement fixture found that the crate could not parse a camelCase `pagemap/1`;
  fixed. `platform/cas/ts` was deleted; the API's `casPath` is the TypeScript side
  of the CAS agreement test.

## W8 — Every template renders in its designed face

**Closes:** the §3.1 font items.

- The worker image installs the OFL faces the templates name that Debian ships:
  `fonts-ebgaramond`, `fonts-libertinus`, `fonts-noto-core`, and Source Serif where
  packaged. `fontvault` registers them.
  - A face Debian doesn't ship moves its template to one it does, rather than waiting on a
    download.
  - A test fails if any template names a face the vault can't resolve.
- **Typst uses the vault:** `paginate-typst` passes `--font-path` for the resolved face
  directories. It stops relying on system discovery.
- **EPUB embeds the OFL faces.** Commercial faces are refused there already by `allowedUses`.
- **As built:** Typst gets a directory of copies of exactly the vault-resolved files, plus
  `--ignore-system-fonts`, rather than the directories they live in: a face's directory is
  usually the whole system font folder, which would hand discovery straight back. The
  template test checks every face is *licensed*; that every face is *installed* can only
  be checked in the image. The `epub` stage gained an optional `designspec/1` root input
  (`tracer_bullet.py` supplies it; the worker's design is the house one). **Debian packages
  (done):** an image showed the guess right and worse. Debian 13 ships EB Garamond as "EB
  Garamond 12", Source Sans as "Source Sans 3", and no Libertinus, Source Serif,
  Merriweather or Fira Mono. The templates now name what the image has: EB Garamond 12,
  Linux Libertine O (Libertinus's parent), Noto Serif (for Source Serif and Merriweather),
  Source Sans 3, Noto Sans Mono, all OFL and registered in the vault. `Dockerfile.worker`
  installs `fonts-ebgaramond fonts-linuxlibertine fonts-noto-core fonts-adobe-sourcesans3`
  and runs a face gate at build time: every face a template names is located the way
  paginate locates it, or the image build fails. It locates all 16; with the old names, none
  of the five replaced families resolved.

## W9 — Ingest loses nothing it can see

**Closes:** the §3.1 ingest list.

- **Bold inherited through a paragraph or character style** counts for the bold-heading
  rule. The style chain is resolved once, as style names already are.
- **A caption typed as prose** directly after a figure ("Εικόνα 6: …", "Figure 3.", "Πίνακας
  2") becomes that figure's caption. Its text moves; it isn't copied, and the integrity gate
  proves it.
- **`w:sym`** maps through the Symbol/Wingdings tables to Unicode. An unmapped symbol is an
  `IngestError`, not a silent drop, and the no-loss oracle counts it.
- **Endnotes** get an `endnote` node (an AST schema addition). They render as notes at the
  chapter's end in print and EPUB, not as footnotes.
- **VML shapes:** their text is kept, as now. Dropped lines and arrows are counted in
  ingest's metrics and reported as a warning (W0).
- **As built:** the Wingdings table maps only its arrows, and to U+2190–2193 rather than
  the U+1F850 sans-serif arrows almost no text face draws; anything unmapped is an
  `IngestError` naming font and code, so the table grows from real books. The shape count
  also covers old VML pictures and OLE objects (counted, not converted). Endnotes stay
  blocks after their citing paragraph, as footnotes are; the renderers' note objects queue
  them and every section renderer flushes the queue, and `ast_text` defers them the same
  way. `design-compile` v12 for the endnote CSS.

## W10 — CI runs the gates that exist

**Closes:** §3.3, §3.4, and the §3.1 EPUB items.

- **Nightly run** (`schedule:` plus `workflow_dispatch:`): the `ReproducibilityChecker` does
  a cold build and a cached build of the synthetic corpus and byte-compares them.
- **EPUBCheck in CI:** a pinned jar with its sha256 checked, so
  `test_epubcheck_accepts_the_epub` runs, not skips.
- **ACE by DAISY** in the same job, pinned. It fails on serious or critical violations.
- **`packages/web` tests:** vitest for `actions.ts` (every refusal path, the op each form
  builds validated against `overrides/1`) and the panel's rendering. The web job runs them.

- **As built:** the checker compared nothing (it hashed a directory), so the comparison is
  new: `compare_runs` in `platform/reproducibility`, driven by `tracer_bullet.py
  --reproducibility` (platform may not import stages). Its first run found Ghostscript's
  clock-made dates, `/ID` and XMP uuid in every press file and proof, wall-clock timestamps in
  every manifest, and a gate that skipped code blocks; all fixed, see REMAINING_WORK §3.4.
  The EPUBCheck step pins the jar and its `lib/` tree by digest. Fixed on the way: the
  `contracts` job's missing `jsonschema` (CI on master was red).
- **ACE as built:** `@daisy/ace` 1.4.6, installed in the `python` job; the suite step fails
  if `test_ace_finds_no_serious_violation` skipped. Its first run, on three corpus books,
  found one serious violation in all of them (no `xml:lang` on the OPF package) and two
  moderate ones (no `schema:accessibilitySummary`; the nav's `epub:type="toc"` without
  `role="doc-toc"`). All three are fixed in `EPUB3Writer` (`epub` v4) and pinned by a test
  that needs no ACE; the books now pass with no findings at any level.
- **Web tests as built:** `actions.test.ts` covers every refusal path and validates the op
  each form builds against `overrides/1` with ajv (and shows the validator can fail);
  `StructureReviewPanel.test.tsx` renders the panel's states with `renderToStaticMarkup`.
  40 tests; the `web` job runs them after `next build`. `next` went to 16.3.8, which clears
  GHSA-vcvr-r3jv-pc5j, the advisory that kept `supply-chain` red.

## W11 — Blocked on something outside the code

Each item stays listed with what unblocks it. None is started as a workaround.

| Item | Blocked on |
|---|---|
| Rivers and hyphen stacks (§2.1) | A page-raster pipeline with glyph positions. W7 makes them report "unmeasured" meanwhile. |
| Raster-diff in CI (§3.1) | Typographer-verified goldens in `corpus/golden/`, which is empty. |
| Compositor agent (§1.2) | The same raster pipeline: a spread patch can't be verified without rendered pages. |
| Equations (§3.1) | A design choice: an OMML → MathML converter (Microsoft's XSLT isn't redistributable) and a renderer case in both paths. Ingest refuses them loudly now. |
| The first real book's EMF figure | The author re-saving "Εικόνα 6" as PNG. |
| Reviewer SSO | W2 gives per-reviewer tokens. An identity provider (OIDC) is a product decision. |

---

## Per-phase gate list

Every phase runs, before it's called done:

```bash
./scripts/test.ps1 -k <phase tests>        # then the whole suite once
python platform/stages/integrity.py
python tools/lint_stage_versions.py HEAD
python tools/lint_docs_claims.py
python tools/lint_schemas.py
python tools/lint_service_deps.py
python tools/lint_tenant_scoping.py
lint-imports --config .importlinter
cd schemas && node codegen/generate.mjs && node codegen/test.mjs   # when a schema changed
cd packages/api && npx tsc --noEmit && npx vitest run               # when the API changed
cd packages/web && npx tsc --noEmit                                 # when the web changed
```

`REMAINING_WORK.md` is updated in the same commit as the phase that closes its item.
