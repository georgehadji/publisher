# Context Architecture — evaluating ICM against what Publisher already has

> **Status: research memo, 2026-08-24. Nothing implemented.** Owns identifier series **C1–C4**.
> Two of the four proposals are amendments to existing `VERIFICATION_PLAN.md` items rather than
> new work; that is called out per item.

The Interpretable Context Methodology (ICM) proposes replacing code-heavy agent frameworks with
a five-layer filesystem hierarchy of markdown files, loaded selectively so an agent's working
context stays in a 2,000–8,000 token band instead of 40,000+.

This memo evaluates it against Publisher specifically. The conclusion is not "adopt" or
"reject" — it is that **Publisher already implements ICM's central idea with a stronger
mechanism, and the one part of ICM it lacks is the one part worth taking.**

---

## 1. The verdict, up front

ICM's core claim is that contracts at stage boundaries plus scoped context loading beats a
monolithic prompt. Publisher agrees with that claim and got there first, by a different route:
`@stage(...)` declarations *are* Inputs/Process/Outputs contracts, and the DAG, contract tests,
cache keys and dev harnesses are all derived from them (`StageRegistry` docstring,
`platform/stages/py/publisher_stages/__init__.py:160`).

The difference is enforcement. ICM's Layer 2 contract is hand-maintained markdown that nothing
checks. Publisher's equivalent is executable data that a CI-blocking lint forces you to version
on every touch. **Where the two overlap, Publisher's mechanism is strictly stronger, and
substituting ICM's would be a downgrade.**

Where they do not overlap — ICM's Layer 3/Layer 4 split, and its contract *shape* — ICM names
things Publisher currently leaves unnamed. That is the transferable part.

| ICM layer | Publisher equivalent | Verdict |
|---|---|---|
| L0 — global identity & map | `CLAUDE.md` folder table | **Already satisfied** |
| L1 — workspace routing | `CLAUDE.md` "Go here for this task" (24 rows) | **Already satisfied**, merged into L0 |
| L2 — stage contract (In/Process/Out) | `StageDeclaration` — `inputs=`, `outputs=`, `description=` | **Already satisfied, and better.** Adopting prose here would actively harm — §3 |
| L3 — immutable reference material | `profiles/`, `templates/`, `schemas/` | **Partial gap** — the folders exist, the immutability is unstated — C2 |
| L4 — working artifacts | CAS blobs under `ctx.cas_root` | **Partial gap** — the boundary is prose, unenforced — C3 |

---

## 2. The context-economics argument does not apply here

ICM's headline benefit is token reduction. Measured against this repo, that benefit is already
banked, so it should not be used to justify the work.

| What | Bytes | ≈ Tokens |
|---|---|---|
| `CLAUDE.md` (always resident) | 8,908 | 2,227 |
| 14 skill frontmatters (always resident) | 6,952 | 1,738 |
| **Resident subtotal** | **15,860** | **≈ 3,965** |
| One skill body, loaded on demand | 3,333–10,311 | 833–2,578 |
| **Working total for a scoped task** | | **≈ 4,800–6,500** |

Claude Code's progressive disclosure already gives Publisher ICM's layered loading: only
name+description of each skill is resident; a body loads when the task matches. The 82,889
bytes of skill bodies never enter context at once.

**Publisher sits inside ICM's stated 2,000–8,000 token target band today.** Any proposal below
must justify itself on correctness, not on token count.

---

## 3. Where adopting ICM literally would cause harm

ICM Layer 2 puts a hand-written `CONTEXT.md` in each stage folder declaring Inputs, Process and
Outputs. For Publisher that specific move is a regression, for three reasons.

**It duplicates a machine-readable source.** `StageDeclaration` already carries `inputs`
(param → schema ID), `outputs` (kind → schema ID), `description`, plus `toolchain`, `fixtures`,
`root_inputs`, `optional_root_inputs`, `terminal_outputs`, `implements`, `placement`,
`memory_budget_mb` and `queue`. A prose copy adds no information and cannot be executed.

**It would silently decouple from the DAG.** The graph is derived by matching one stage's output
schema ID to another's input schema ID. A `CONTEXT.md` saying `inputs: ast/1` while the decorator
says `ast/2` produces no error anywhere — the DAG follows the decorator and the agent reads the
markdown. This is the failure mode `implements=` was added to prevent at the code level
(`__init__.py:139`), reintroduced one layer up.

**It recreates a drift class this repo has already paid for.** Commit `ca9caf3` corrected roughly
twenty hand-transcribed facts in freshly written skill files — two fixture-ownership rows were
wrong the day they were committed. `@stage(version=N)` numbers were stripped from those tables
in the same commit precisely because `tools/lint_stage_versions.py` *forces* them to change on
every touched stage module, so a prose copy rots by construction. Seventeen hand-written stage
contracts would rot the same way, faster.

There are currently **233 hand-written table rows** across the 14 skills. That is the existing
drift surface. ICM as written would add seventeen more files to it.

> The repo's own doctrine — facts are derived, and the things guarding them must be able to
> fail — is the reason to decline here. It is also the reason the rest of this memo has
> anything to offer.

---

## 4. What ICM genuinely contributes

### C1 — Give G5's generator ICM's contract shape *(amends `VERIFICATION_PLAN.md` G5)*

`VERIFICATION_PLAN.md` G5 already proposes deriving the skill tables from `get_registry()`
rather than transcribing them. It specifies the *source* but leaves the *shape* open.

ICM answers that. Generate, per stage, an Inputs / Process / Outputs block — ICM's ergonomics,
Publisher's guarantees:

```
### ast-assemble
Inputs   html: typescript-html/1 · source: raw-source/1
Process  Verify text-integrity between extract's HTML and the source AST,
         then emit the canonical AST
Outputs  ast: ast/1 · integrity-report: integrity-report/1 (terminal)
Fixtures fixtures/structure/v1     Queue q.structure     Budget 128 MB
```

Every field above is a `StageDeclaration` attribute read at generation time. `Process` is the
existing `description=`. Nothing is transcribed, so nothing rots, and the lint that gates it
can fail.

This is a one-paragraph amendment to G5, not new work. **It is the whole of ICM's method that
survives contact with this codebase** — and it survives only because it is generated.

### C2 — Name the Layer 3 boundary: reference data an agent must not invent *(~0.25 day)*

ICM's sharpest distinction is L3 (immutable constraints you emulate) vs L4 (data you transform).
Publisher has both physically and names neither.

`grep` across `CLAUDE.md` and the reference-folder skills finds exactly one immutability
statement — `schemas/` is "source of truth" (`CLAUDE.md:20`). `profiles/` and `templates/` carry
none.

That gap has teeth. `profiles/` holds vendor output specs — KDP, IngramSpark and Lulu trim,
bleed, spine and gutter values. An agent that plausibly infers a bleed value instead of reading
the YAML produces a book that passes preflight and is **rejected by the vendor**, which is the
one class of defect the two hard gates cannot catch, because the artifact is internally
consistent and externally wrong.

**Fix.** One sentence in `CLAUDE.md`'s architecture section and one line in each of the three
skills' frontmatter, stating that `profiles/`, `templates/` and `schemas/` are read-only
reference data: values are looked up, never inferred, and changing one is a deliberate product
decision. Cheap, and it closes a gap the derived-DAG doctrine does not reach.

### C3 — Make the Layer 4 boundary enforceable, not just documented *(~0.5 day)*

`CLAUDE.md` states: *"Stages write via `ctx.cas_root` — never `ctx.work_dir`, which is scratch
and is deleted."* That is exactly ICM's L4 rule, correctly identified.

It is also unenforced prose. `tools/` holds three lints — schema free-text, stage version bump,
service deps — and none covers this. Ten of eleven stage modules use `ctx.cas_root`;
`finish_stage.py:93` and `prepress_stages.py:317` use `ctx.work_dir`, legitimately, as scratch.
So the rule has real exceptions, which is precisely why prose is the wrong medium: a reader
cannot tell a permitted scratch use from a bug that loses an artifact.

**Fix.** A fourth lint in `tools/`, following the conventions of the existing three, that flags
a `ctx.work_dir` path reaching a CAS write or a returned `StageResult` artifact, with an
explicit allowlist for the two known scratch uses. This turns a documented convention into a
gate that can fail — the same move `VERIFICATION_PLAN.md` G4 makes for the skills themselves.

### C4 — Decline the rest, explicitly *(no work)*

Recording this so it is not re-proposed:

- **Numbered stage folders** (`01_research/`, `02_script/`). Publisher's execution order is a
  derived fixpoint over schema IDs, not a directory sort. Numbering folders would encode a
  sequence the executor ignores and that changes whenever a stage's `inputs=` changes.
- **Root `CONTEXT.md` split from `CLAUDE.md`.** ICM separates L0 and L1; Publisher merged them
  into a single file carrying both tables. Splitting adds a hop and a second file to keep in
  sync for no measurable gain — resident context is already inside ICM's band (§2).
- **ICM's "stop and notify" review gates.** Publisher's gates are stronger: text integrity fails
  the build with no override flag, and `package` declares `preflight_report` as a required input
  so a build physically cannot be packaged without a verdict. ICM's convention is an agent that
  agrees to stop; Publisher's is a graph that cannot proceed.
- **ICM's human-in-the-loop U-curve** is already served by `services/structure/publisher_structure/overrides.py`
  (human and agent edits never mutate the AST) and `packages/web/src/review/`.

---

## 5. Relationship to `VERIFICATION_PLAN.md`

This memo does not supersede that plan and adds almost nothing to its budget.

| Item | Disposition |
|---|---|
| C1 | Amends G5 — specifies output shape. No added cost. |
| C2 | New, ~0.25 day. Independent of every G item. |
| C3 | New, ~0.5 day. Sits alongside G4, same doctrine, different target. |
| C4 | Decisions only. |

Net new work: **~0.75 day.** G1 (CI can execute the suite) remains the precondition for
verifying any of it, and the GitHub runner outage remains the precondition for G1.

---

## 6. Non-goals

- No stage version bumps. Nothing here changes a `@stage(...)` declaration.
- Neither hard gate is touched. `allow_stub_engines` stays off.
- No new prose documentation. C1 replaces hand-written tables with generated ones; C2 adds four
  sentences; C3 adds a lint, not a document.
- The 14 skills are not restructured. They were corrected in `ca9caf3` and their hand-written
  half — the "why it is shaped this way" paragraphs and the recorded mistakes — is the half
  that does not rot and should stay hand-written.

---

## 7. Expected outcome

ICM's contribution to Publisher is not its file structure, which duplicates a stronger
mechanism, and not its token economics, which are already realised. It is one shape and one
vocabulary: a per-stage contract block worth generating into, and a name for the
reference-versus-artifact boundary that this repo enforces in code but nowhere states to the
agent reading it.

Taken that way, ICM costs under a day and closes a defect class the derived-DAG doctrine cannot
reach — an agent inventing a vendor value that every internal gate accepts. Taken literally, it
would add seventeen unverified prose contracts to a repo whose central discipline is that facts
are derived and their guards must be able to fail.
