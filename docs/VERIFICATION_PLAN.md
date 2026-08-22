# Publisher — Verification Plan (G1–G8)

**Status:** draft — nothing implemented. Written 2026-08-22.
**Baseline:** the review of `claude/folder-skills-content-map-grbvip` (commits `4d6d4f0`,
`ca9caf3`), plus CI archaeology back to `ba1a0cb` (2026-08-11).
**Supersedes nothing.** Continues the gate doctrine of the completed
[REMEDIATION_PLAN.md](REMEDIATION_PLAN.md) and [ARCHITECTURE_REMEDIATION.md](ARCHITECTURE_REMEDIATION.md);
orthogonal to [ARCHITECTURE_UPLIFT_PLAN.md](ARCHITECTURE_UPLIFT_PLAN.md) (U8–U9 still open).
This document owns the **G-series (G1–G8)**. No other plan uses `G`.

---

## 1. The shape of the problem

This repo applies one idea rigorously to the build graph: **facts are derived, and the
things that guard them must be able to fail.** The DAG is derived from `@stage(...)`
declarations. Contract tests are generated from the registry. `integrity.py` re-derives
reachability. Three lints block CI. Two hard gates have no override flag.

None of that discipline reaches the layer that tells humans and agents *how to work on the
pipeline* — `CLAUDE.md`, `.claude/skills/`, module docstrings, `.gitignore` comments, doc
section numbers. That layer is hand-written, hand-maintained, and ungated. And underneath
it, the one mechanism that would notice — CI — has been unable to execute a Python test
since 2026-08-11.

So there are two strata, and the lower one hides the upper:

**Stratum 0 — CI cannot execute.** `.github/workflows/ci.yml` installs the editable
packages but never installs `pytest`, so `python -m pytest` dies instantly with
`No module named pytest`. Both the `python` and `contracts` jobs have failed this way on
every push for eleven days. The failure is sub-second and produces no test output, which is
exactly why it reads as noise rather than as an outage.

**Stratum 1 — the instruction layer has no gate even when CI works.** Three separate
"gates" in this repo currently pass by construction:

| Gate | Why it cannot fail |
|---|---|
| `gen:check` (`git diff --exit-code -- '**/*.gen.*'`) | `.gitignore:29` ignores `*.gen.*`; zero are tracked, so `git diff` has nothing to compare |
| `lint_stage_versions.py --base <ref>` | the script reads `sys.argv[1]` positionally; `--base` becomes the ref, `git diff` fails, and the "not a git repo" branch returns `[]` |
| SKILL.md frontmatter | nothing validates it at all |

**The evidence that this is not theoretical.** The commit that added `.claude/skills/`
shipped four files whose YAML frontmatter does not parse, and roughly twenty false claims
about the codebase — including an instruction to re-add a stage list to
`platform/stages/integrity.py` that had been *deliberately deleted* after it drifted. A
hand-rolled regex "validation" reported all fourteen files valid. That is the same failure
mode as the three rows above: a check that cannot fail, believed because it was green.

**Therefore the fix is not more prose review.** It is: make CI able to run (G1), make every
gate prove it can fail (G2, G3), derive what is derivable instead of transcribing it (G4),
and correct the stale sources that seeded the wrong text in the first place (G5–G6).

### Precondition, not a workstream

A GitHub runner-level outage has been live since ~17:13 UTC 2026-08-21: jobs complete in
2–4 s with no `steps` array, no runner assigned, and 404 logs, on every branch including
ones with no Python or Rust changes. **Nothing in this plan is verifiable until that
clears.** Do not interpret a 2-second red check as a G-series failure; a run that genuinely
executed has a populated `steps` array and an assigned `runner_id`.

---

## 2. Method

Inherit the working rule from `ARCHITECTURE_REMEDIATION.md` §2:

> **Land each detector in the same PR as its fix, detector first in the diff.**

With one addition specific to this plan, earned the hard way:

**Prove the gate can fail before trusting it.** Every G-item below states a command that
must be **red today** and green after. If a proposed gate cannot be made red on purpose, it
is not a gate — it is decoration, and it will be believed anyway. Three of this repo's
existing gates were green-because-broken; a fourth (`platform/stages/integrity.py`) was
invoked in a form that could not run. Adding a fifth unverifiable one is worse than adding
nothing.

**Corollary for G4:** the obvious model for derived documentation is the schema codegen
pattern — `generate` then `git diff --exit-code`. **Do not copy that pattern until G2 has
fixed it.** It is currently the broken one, and replicating it would produce a second gate
that cannot fail.

---

## 3. Workstreams

### G1 — CI can execute the suite  *(~0.5 day)*

**Symptom.** The `python` and `contracts` jobs fail in under a second after a successful
26-second install, on every commit since `ba1a0cb`.

**Evidence.** `.github/workflows/ci.yml` runs `pip install ${{ env.PUBLISHER_PKGS }}` —
fourteen `-e` packages, none with `[test]` extras — then `python -m pytest --tb=short`.
Nothing installs `pytest`. Reproduced in a clean 3.12 venv: `No module named pytest`.
Installing pytest exposes the next layer: `tests/integration` fails collection on
`psycopg2` (pinned in `requirements.txt`, which CI never installs) and then on `requests`
(not in `requirements.txt` at all).

**Fix.**
1. Add `requests>=2.31,<3` to `requirements.txt`. It is a genuine test-path dependency and
   the file's own header says it is "the first place third-party deps are actually pinned".
2. In both the `python` and `contracts` jobs, after the editable installs:
   `pip install -r requirements.txt pytest`.
3. Scope the default run to what CI can actually provision:
   `python -m pytest --tb=short --ignore=tests/integration`.

**On step 3 — this is a stopgap, and it must be labelled one in the workflow file.**
Excluding a test directory to get green is the move this repo's doctrine is most hostile
to. It is acceptable here only because the excluded tests require infrastructure CI has
never had, and because **G8 is the commitment to restore them**. It is not acceptable to
leave `--ignore` in place once G8 lands, and it is never acceptable to widen it to a test
that merely fails.

**Gate.** Red today: `python -m pytest --tb=short` in a CI-equivalent env exits non-zero
with a collection error. Green after: 348 tests pass. A CI run whose `python` job reports a
test count is itself the proof — the current failure produces none.

---

### G2 — The codegen gate can fail  *(~0.5 day)*

**Symptom.** `gen:check` certifies that generated Pydantic/Zod types are in sync. It exits
0 unconditionally.

**Evidence.** `.gitignore:28-29` reads `# Generated code (checked in but must be
regenerated with gen:check)` immediately above `*.gen.*`. The comment and the rule
contradict each other, and the rule wins: `git ls-files | grep '\.gen\.'` returns nothing,
`git add` refuses the files, and `git diff --exit-code -- '**/*.gen.*'` inspects only
tracked paths. CI's variant (`node codegen/generate.mjs` then a bare `git diff
--exit-code`) is equally blind to ignored files.

**Fix — decide the intent, then make the mechanism match it.** Two coherent options; pick
one, do not keep both halves:

- **(a) Generated types are build output.** Keep them ignored. Delete the misleading
  `.gitignore` comment and the `gen:check` script, and make `node codegen/test.mjs` — which
  actually executes the generated types — the CI gate. Simplest, and honest about what is
  verified.
- **(b) Generated types are checked in.** Remove `*.gen.*` from `.gitignore`, commit both
  files, and keep `gen:check`. This gives real drift detection across languages at the cost
  of generated files in review diffs.

**Recommendation: (a).** The repo already treats derived artifacts as content-addressed
build output rather than source (that is the whole CAS argument), and `codegen/test.mjs`
was specifically rewritten to *execute* the output after a previous version passed while
the generated TypeScript had 39 dangling references. Executing beats diffing here.

**Gate.** Red today: introduce a deliberate schema/codegen mismatch and confirm the *new*
gate reports it. Under (a) that means `node codegen/test.mjs` must fail on a hand-broken
generator; under (b), `gen:check` must fail on a stale committed file. Whichever is chosen,
the mismatch must be demonstrated red before the item is closed.

---

### G3 — The version-bump lint's interface matches its documentation  *(~0.25 day)*

**Symptom.** `python tools/lint_stage_versions.py --base HEAD~1` prints "no stage files
changed since --base" and exits 0, having compared nothing.

**Evidence.** `tools/lint_stage_versions.py:60` is
`base = sys.argv[1] if len(sys.argv) > 1 else "HEAD~1"` — no `argparse`. The literal string
`--base` is passed to `git diff --name-only`, which fails, and the "Not a git repo or no
base — skip" branch returns an empty change list. The script's **own docstring (line 9)**
documents the broken form, which is where the skill copied it from. CI happens to invoke it
positionally, so the bug has never bitten in CI — only anyone following the docs.

**Fix.** Add `argparse` with a real `--base` flag *and* keep the positional form working
(CI uses it). Then make the failure mode loud: a `git diff` that exits non-zero must raise,
not silently return `[]` — that except-branch is the actual defect, and it would swallow a
genuine git failure in CI too. Correct the docstring.

**Gate.** Red today: `--base HEAD~1` exits 0 on a tree with an unbumped stage change. Green
after: both `--base HEAD~1` and `HEAD~1` flag it, and an unparseable ref exits non-zero
with a message rather than passing.

---

### G4 — The instruction layer gets a gate  *(~1 day)*

**Symptom.** Nothing validates `.claude/skills/*/SKILL.md`. Four files with unparseable
YAML shipped and loaded fine, because Claude Code's frontmatter parser is lenient where the
spec-compliant tooling is not.

**Evidence.** `yaml.safe_load` raises `ScannerError` on four of the fourteen descriptions —
each an unquoted scalar containing `: `. `quick_validate.py` (the same function
`package_skill.py` gates packaging on) rejected all four. The repo has three CI-blocking
lints for schemas, stage versions and service deps, and zero for the files that instruct
the agent.

**Fix.** Add `tools/lint_skills.py`, sitting alongside the existing three and following
their conventions (standalone script, non-zero on violation, no third-party deps beyond
what CI already has). It checks, per skill directory:

- frontmatter parses as YAML and has exactly `{name, description}`;
- `name` matches the directory and is valid kebab-case;
- `description` is within the length limit;
- every backticked repo path in the body resolves on disk, with an explicit allowlist for
  the deliberate exceptions (generated files, `corpus/golden/`);
- markdown tables have consistent column counts (catches unescaped `|` inside code spans).

Wire it into the `contracts` job next to the other three lints.

**Why a path check earns its place.** It is the cheapest possible detector for the largest
error class actually observed: of the ~20 false claims in the baseline commit, the ones that
would have wasted the most agent time were paths and file attributions. It cannot catch a
wrong *description* of a real file — G5 and human review own that.

**Gate.** Red today: run it on `4d6d4f0` and it must flag the four YAML failures. Green
after: clean on `HEAD`, and still red on a deliberately introduced bad path.

---

### G5 — Derive the skill tables that are derivable  *(~1.5 days, after G2)*

**Symptom.** Stage I/O, fixture-set ownership, schema IDs, route inventories and test counts
are transcribed by hand into `.claude/skills/` with no detector. Two fixture rows were wrong
on the day they were committed.

**Evidence.** `publisher-fixtures` claimed `structure/v1` was declared by no stage (it is
`ast-assemble`'s) and that `cover/v1` was declared by `cover` (which sets `fixtures=None`,
making the set an orphan that yields zero contract cases). Both facts are one
`get_registry()` lookup away. Stage `version=` numbers were removed from the tables in
`ca9caf3` precisely because `lint_stage_versions.py` *forces* them to change on every
touched stage module — a prose copy rots by construction.

**Fix.** A generator that emits marked blocks into the skill files from the real sources —
`get_registry()` for stage names, I/O schema IDs, `implements`, `terminal_outputs` and
`fixtures=`; the fixture manifests for set contents; a route scan for the API table. Then
gate it the way G2 settled on.

**This is the same doctrine the DAG already has**, applied one layer up: the graph is
derived from declarations rather than hand-wired, and so should its documentation be. The
hand-written half then shrinks to what is genuinely non-derivable and genuinely valuable —
the "why it is shaped this way" paragraphs and the recorded mistakes — which do not rot.

**Sequencing note.** Blocked on G2 by design. Do not build this on
`generate → git diff --exit-code` while that pattern is broken.

**Gate.** Red today: change a stage's `outputs=` and confirm the skill table goes stale
with no signal. Green after: the same change fails the diff-check until the tables are
regenerated.

---

### G6 — Correct the stale sources  *(~0.5 day)*

**Symptom.** Several wrong claims in the skills were *faithfully copied* from wrong claims
in the code. Fixing the copies without fixing the sources guarantees the next writer
repeats them.

**Evidence and fixes:**

| Source | Wrong claim | Fix |
|---|---|---|
| `stages/__init__.py:13` | "Keep this list in sync with `platform/stages/integrity.py`" | integrity.py does a bare `import stages` and has no list; delete the sentence |
| `tools/lint_stage_versions.py:9` | documents `--base <git-ref>` | corrected by G3 |
| `.gitignore:28` | `*.gen.*` "checked in" | corrected by G2 |
| `platform/pagescan/src/lib.rs:3` | cites `BUILD_PLAN.md §3.13` | §3.13 is `packages/orchestrator`; pagescan is covered in §3.9 |
| `services/structure/publisher_structure/overrides.py:4` | cites `BUILD_PLAN.md §3.17` | §3.17 is `services/agents`; the override layer is §3.7. (Its `ARCHITECTURE.md §2.6` citation is correct — leave it.) |
| `scripts/test.ps1`, `scripts/test.sh` | mode 100644 | `chmod +x`, so the documented `./scripts/test.sh` works |
| `scripts/test.ps1:14` | "338 collected" | unverified against the current tree; re-measure under G1 or drop the figure |

**Gate.** `grep` for each corrected string returns only the corrected form. The `chmod` is
verified by `./scripts/test.sh --collect-only -q` running rather than returning
"Permission denied".

---

### G7 — One folder map, and a context budget  *(~0.5 day; needs a decision)*

**Symptom.** The repo now has three folder maps. Two are stale and were not reconciled when
the third was added.

**Evidence.** `README.md:62-64` and `docs/ARCHITECTURE.md:436-451` both list
`packages/orchestrator`, `packages/worker-render`, `services/design`, `platform/telemetry`,
`platform/fontvault` and `infra/`. **All six were verified absent from disk.**
`ARCHITECTURE.md` §2.14 also names stage files `*.stage.py|ts` where the convention is
`*_stage.py`.

Separately, always-loaded context grew by roughly 2,850 tokens per request — `CLAUDE.md`
66 → 122 lines, plus 14 skill descriptions resident in every session.
`COST_AND_STABILITY_PLAN.md:86` sizes this file at "**a ~60-line project file**". The
folder table's third column also paraphrases the skill `description:` fields it sits beside
(~82% word overlap), so that content is loaded twice.

**Fix.**
1. `CLAUDE.md` is the live map (it is the only one under a gate once G4 lands). Replace
   `ARCHITECTURE.md` §2.14's tree and `README.md`'s "Project structure" with a pointer to
   it, or delete them.
2. Drop the folder table's "What lives there" column — the skill descriptions already say
   it, and they are what the model matches on. Recovers ~400 tokens and removes a two-file
   edit from every folder change.
3. **Decision required:** whether to also drop or shorten the 24-row task-routing table
   (~800 tokens). It is the single most useful thing in the file for a cold start, and the
   most expensive. Recommendation: keep it, and pay for it with (1) and (2), landing around
   90 lines rather than 122. Not the 60-line target, but defensible against it.

**Gate.** No map outside `CLAUDE.md`; `wc -l CLAUDE.md` under the agreed ceiling; every
folder in the table exists on disk (G4 enforces this).

---

### G8 — Integration tests get real infrastructure  *(~1.5 days)*

**Symptom.** `tests/integration/` — the U1/U2/U5/U7 gates, the tests that prove worker
durability and tenant isolation — has never run in CI. G1 excludes it explicitly.

**Evidence.** With pytest, `requirements.txt` and `requests` all installed, the remaining
failures are `psycopg2.OperationalError: connection to server at "localhost" ... port 55432
failed` and `FileNotFoundError: packages/api/node_modules/.bin/tsx`. The suite needs a live
Postgres and a built API; `tests/integration/conftest.py` already defaults `DATABASE_URL`
to the compose-published port and is env-overridable, so the harness side is ready.

**Fix.** A fourth CI job: a `postgres:16-alpine` service container with
`platform/db/schema.sql` applied, `npm ci` in `packages/api`, `DATABASE_URL` pointed at the
service, and `python -m pytest tests/integration`. Then remove `--ignore=tests/integration`
from G1's step — that removal is the completion criterion for G1's stopgap.

**Gate.** Red today: the job does not exist. Green after: it runs and passes, and
`git grep -- '--ignore=tests/integration'` returns nothing in `.github/`.

---

## 4. Sequencing

```
runner outage clears  (external — precondition for verifying anything)
        │
        ├── G1  CI executes ─────────────────┐
        ├── G3  lint interface               │  (independent, cheap, no deps)
        ├── G6  stale sources                │
        │                                    │
        ├── G2  codegen gate can fail ───┐   │
        │                                ↓   ↓
        ├── G4  skills lint ─────────► G5  derived tables
        │
        ├── G7  one map + context budget     (needs a decision first)
        └── G8  integration infra ──────────► removes G1's --ignore stopgap
```

**Order of value.** G1 first and alone — until CI runs, no other item can be shown to work.
G3 and G6 are cheap and independent; land them alongside. G2 gates G5. G8 closes G1's
stopgap and should not be deferred indefinitely, because a permanently-excluded test
directory is how a suite quietly stops covering its own hardest guarantees.

**Total: roughly 6 days**, of which G1 (half a day) unblocks the ability to verify any of
the rest.

---

## 5. Non-goals

- **The two hard gates are untouched.** Text integrity in `ast-assemble` and preflight
  before `package` have no override flag by design, and nothing here adds one.
- **No stage behaviour changes.** No `@stage(version=)` bumps should result from this plan;
  if one does, something in scope was misjudged.
- **`allow_stub_engines` stays a dev-only escape hatch**, default `False`, unset in the
  worker. Nothing here needs it.
- **No new prose documentation.** The problem is unverified claims, not insufficient ones.
  G5 shrinks the hand-written surface rather than growing it.
- **U8–U9 remain out of scope** — bounded concurrency and load proof belong to
  `ARCHITECTURE_UPLIFT_PLAN.md`, not here.
- **The GitHub runner outage is not a workstream.** It is external. If it persists, G1's
  correctness can still be verified locally in a 3.12 venv, but its *effect* cannot be
  observed.

---

## 6. Expected outcome

CI executes and reports a real test count. Four gates that could not fail either can fail
or no longer exist. The instruction layer gains the same two properties the build graph has
had all along: the parts that can be derived are derived, and the parts that cannot are at
least checked for referring to things that exist.

The narrower claim worth holding to: **after this plan, a green check means something it
does not mean today.**
