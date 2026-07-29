# Publisher — Remediation Plan

Fix plan for the 14 findings in the independent verification of the DeepSeek implementation against [BUILD_PLAN.md](BUILD_PLAN.md) v2.1 and [ARCHITECTURE.md](ARCHITECTURE.md).

Companions: [BUILD_PLAN.md](BUILD_PLAN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [OPTIMIZATION.md](OPTIMIZATION.md) · [implementation_audit_report.md](implementation_audit_report.md) (superseded on two items — see §1.2).

**Baseline verified 2026-07-29.** 203 Python tests + 6 skipped, 18 Rust tests, all passing. Tracer bullet green. The scaffolding is sound; module boundaries, the error taxonomy, the sandbox fixtures, and most stubs are honest. This plan does not rebuild any of that.

---

## 1. The shape of the problem

### 1.1 One theme, not fourteen unrelated bugs

Every high-severity finding is the same failure in a different place: **a gate exists, is named correctly, reports success — and cannot fail.**

- The text-integrity gate compares the AST to a hash stored inside the same AST file.
- The structure stage that owns that gate runs *after* `package`, with no downstream consumer.
- The preflight gate is not in the pipeline at all.
- The generated contract tests are dicts describing tests that are never executed.
- The DAG integrity check that would have caught the above does not exist.
- There is no CI, so nothing that says "CI-enforced" in the plan is enforced.

This is precisely the drift [BUILD_PLAN §3.20](BUILD_PLAN.md) exists to prevent: *"without it, 'every stage is independently developable against fixtures' is a convention people drift from under deadline."* The registry was built; the enforcement half of it was not.

**Consequence for the fix strategy.** Adding more checks is the wrong move — checks are what already exist and don't bite. The fixes below restore enforceability **structurally**, so the gates cannot be bypassed by construction (D2: make illegal states unrepresentable), and only then add assertions.

### 1.2 Two prior audit findings are stale

[implementation_audit_report.md](implementation_audit_report.md) leads with "the DAG executor never wires upstream outputs into downstream inputs" (§7.1, High) and "seven hardcoded fixture paths" (§7.1, Medium). **Both have since been fixed** — [tracer_bullet.py:99-104](../tracer_bullet.py:99) resolves declared inputs from upstream artifacts, and `initial_inputs` is now only the root stage. That report also marks O4 and the generated contract tests ✅ when neither is complete. Treat it as a point-in-time snapshot, not current state.

### 1.3 Findings → fixes

| # | Finding | Sev | Fix |
|---|---|---|---|
| 1 | Text-integrity gate is self-certifying; skips silently when hash absent | **Critical** | F2.2 |
| 2 | `structure` runs last, after `package`; `ast/1` has no consumer | **Critical** | F2.1 |
| 3 | `preflight` never runs in the pipeline | **Critical** | F2.3 |
| 4 | No DAG orphan / cycle / duplicate-producer checks | **High** | F1.1 → F2.1 |
| 5 | `derive_contract_tests()` returns descriptors, never executed | **High** | F1.2 |
| 6 | No CI at all | **High** | F0.2 |
| 7 | `pytest` from root collects nothing (`testpaths=["tests"]`) | **High** | F0.1 |
| 8 | `preferredEngine: "typst"` declared everywhere; no Typst emitter exists | **Medium** | F4.1 |
| 9 | O4 half-done — `FileSizeBudget` present, press/proof pair absent | **Medium** | F4.2 |
| 10 | `runningHeadText` free-text field in a structure-route schema; no lint rule | **Medium** | F3.1 |
| 11 | `"value": {}` unbounded in agent-proposal schema | **Medium** | F3.2 |
| 12 | No zero-AST-write / subagent-cap / task-budget adversarial tests | **Medium** | F3.3 |
| 13 | No `placement` field on `@stage` (v2.1 §3.20/§4.6) | **Low** | F4.3 |
| 14 | `finish` reports `passed` with `profile_applied: "none"` | **Low** | F2.4 |

---

## 2. Method: detectors first, in the red

The repo's own doctrine mandates test-first (`CLAUDE.md`, tdd-guide) and [§3.20](BUILD_PLAN.md) makes generated contract tests *the merge gate*. So the phases below are ordered so that **F1 writes detectors that fail against today's code**, converting all 14 findings into failing assertions, and F2–F4 turn them green.

This ordering is not ceremony. It buys three things:

1. A finding that cannot be expressed as a failing test is a finding nobody can prove is fixed — or prove stays fixed.
2. The DAG integrity checker (F1.1) *is* the fix-verification for the rewire (F2.1). Writing it second would mean hand-verifying the rewire.
3. It makes the remediation itself auditable: `git log` shows red → green per finding.

**Working rule for every fix below: land the failing check in the same PR as the fix, check first in the diff.**

---

## F0 — Make the suite runnable and enforced (½ day)

Nothing below is verifiable until this lands. Cheapest, first, unblocks everything.

### F0.1 — Fix test discovery *(finding 7)*

**Root cause.** `pyproject.toml` declares `testpaths = ["tests"]`; no root `tests/` directory exists. Tests live in per-module `*/tests/`. Bare `pytest` collects zero and exits 0 — the failure mode that lets a suite look green when it never ran.

**Change.** Point `testpaths` at the real locations:

```toml
[tool.pytest.ini_options]
minversion = "8.0"
addopts = "-q --strict-markers"
testpaths = ["platform", "services", "packages", "stages"]
```

**Acceptance.** `pytest` from the repo root, with no arguments, collects ≥ 221 tests. A CI assertion pins the floor so a future collection break fails loudly rather than silently:

```python
# tests/test_collection_floor.py
def test_suite_is_actually_collected(pytestconfig):
    """A collection regression must fail, not silently pass with 0 tests."""
    assert pytestconfig.option.testpaths or True  # placeholder; see CI step below
```

Prefer the CI-level form — `pytest --collect-only -q | tail -1` parsed against a floor — over an in-suite meta-test that can itself be skipped.

### F0.2 — CI skeleton *(finding 6)*

**Root cause.** No `.github/workflows`. Every gate the plan calls "CI-enforced" — version-bump rule, codegen sync, cost regression, ratchet, DAG integrity — is currently documentation.

**Change.** One workflow, jobs matching the plan's declared gates. Start with the four that are implementable today; the rest land with their phases.

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]
jobs:
  python:
    steps:
      - pytest (collection floor asserted)
      - ruff check
  rust:
    steps: [cargo test, cargo clippy -- -D warnings]
  contracts:
    steps:
      - pnpm gen && git diff --exit-code      # codegen in sync (§3.1 Done)
      - python -m platform.stages.integrity   # DAG integrity (F1.1)
      - pytest tests/contracts                # generated contract tests (F1.2)
  doctrine:
    steps:
      - python tools/lint_schemas.py          # no free-text in structure routes (F3.1)
      - python tools/lint_stage_versions.py   # version-bump rule (§3.3, §7)
```

**Note on the version-bump rule.** [BUILD_PLAN §3.3](BUILD_PLAN.md) requires "touched stage dir ⇒ VERSION must change". Implement as: diff `stages/` and `services/` against the merge base; for each touched stage module, assert its `@stage(version=N)` differs from the base revision. This is the guard against the cache-correctness bug in the risk register, and it is cheap.

**Acceptance.** CI red on the current tree (it will be — F1's detectors fail), green after F4.

---

## F1 — Detectors (2 days, all expected to FAIL on landing)

### F1.1 — DAG integrity checker *(finding 4)*

**Root cause.** [`derive_dag()`](../platform/stages/py/publisher_stages/__init__.py:152) does `if schema_id in producers` — silently dropping any input with no producer. `topological_sort()` marks nodes visited on entry with no recursion stack, so a back-edge is skipped rather than detected. Neither duplicate producers nor orphan outputs are surfaced.

[BUILD_PLAN §3.20](BUILD_PLAN.md) Failure clause: *"A declared input schema with no producing stage, an orphan, or a cycle fails CI."* Three required checks, zero implemented.

**Change.** Add `StageRegistry.check_integrity() -> list[DagViolation]` returning, never raising (D3 — errors are values at boundaries):

| Violation | Rule | Current tree |
|---|---|---|
| `unsatisfiable_input` | every declared input schema has ≥ 1 producer, or is declared a root input | `package` needs `manifest/1` — no producer |
| `orphan_output` | every declared output is consumed, or the stage is declared terminal | `structure` emits `ast/1` — no consumer |
| `ambiguous_producer` | ≤ 1 producer per schema id unless the consumer selects explicitly | `pdfx/1` from both `finish` and `finish-gs` |
| `cycle` | proper DFS with a recursion stack | none today |
| `non_schema_input` | input values must be registered schema ids | `cover` declares `"integer"`, `"image/*"` |

Root and terminal stages must be *declared*, not inferred — `@stage(..., root_inputs=["manifest_path"])` and `terminal=True`. Inferring them means the checker cannot distinguish "intentional entry point" from "forgot to wire it", which is exactly finding 2.

**Acceptance.** `check_integrity()` returns ≥ 5 violations on the current tree; CI job fails. After F2.1, returns `[]`.

### F1.2 — Execute the generated contract tests *(finding 5)*

**Root cause.** `derive_contract_tests()` returns `list[dict]` and **is called nowhere in the codebase**. [§3.20](BUILD_PLAN.md) Done: *"contract tests 100 % generated (zero hand-written)."* Descriptors are not tests.

**Change.** A single collector that turns the registry into real parametrized tests:

```python
# tests/contracts/test_generated.py
import pytest
from publisher_stages import get_registry
import stages  # noqa: F401 — registers every stage

CASES = [(t["stage"], t["fixtures"]) for t in get_registry().derive_contract_tests()]

@pytest.mark.parametrize("stage_name,fixture_dir", CASES, ids=lambda c: str(c))
def test_stage_output_matches_declared_schema(stage_name, fixture_dir):
    """Run the stage against its fixture set; validate every artifact
    against the schema its declaration promises."""
    ...  # execute stage, jsonschema-validate each artifact by declared schema id
```

Two hard requirements, both from the plan:
- **A stage with a declared `fixtures` path and no fixture data on disk fails registration** (§3.20 Failure). Today missing fixtures are silently absent from `CASES` — a stage can dodge its contract test by shipping no fixture.
- Validation uses the **compiled** validator (`fastjsonschema`), per §3.1, not reflective walking.

**Acceptance.** Every registered stage appears as a test case. Stages whose fixtures don't exist fail — expected on landing, since `fixtures/structure/v1`, `fixtures/paginate/v1`, `fixtures/finish/v1` are declared but the tree only ships `fixtures/ast`, `fixtures/design-compile`, `fixtures/preflight`.

### F1.3 — Integrity-gate property tests *(finding 1)*

Write these before touching `structure_stage.py`. All three must fail now:

```python
def test_dropped_paragraph_is_detected():
    """The gate's entire purpose: AST missing source text must fail."""
    # source with 10 paragraphs, AST with 9 → expect StageError

def test_missing_integrity_hash_is_hard_failure():
    """No hash is not a pass. Plan: '0, always. No flag, no override, no config.'"""
    # AST without integrityHash → expect StageError, NOT passed:True

def test_normalizer_is_nfc_and_quote_folding():
    """Smart vs straight quotes and NFD vs NFC must compare equal."""
    assert _normalize("don’t é") == _normalize("don't é")
```

### F1.4 — Adversarial agent tests *(finding 12)*

[§3.17](BUILD_PLAN.md) Tests and [§7](BUILD_PLAN.md) DoD both require these; none exist.

```python
def test_agent_cannot_propose_ast_write():
    """Action space is override ops only. Any AST-mutating proposal rejected."""
def test_agent_respects_subagent_cap():
    """Uncapped Compositor spawns one subagent per spread (§3.17)."""
def test_agent_halts_at_task_budget():
    """Budget exhaustion wraps up gracefully, does not run unbounded."""
```

`test_proposal_requires_human_gate` already exists and tests the gate, not the action space — keep it, it is not a substitute.

---

## F2 — Restore the gates (4 days)

### F2.1 — Rewire the DAG to the architecture's real chain *(findings 2, 4)*

**Root cause.** The implementation collapsed the pipeline and inverted a dependency. [ARCHITECTURE §1.2](ARCHITECTURE.md) specifies:

```
extract → structure-rules → structure-llm → ast-assemble → resolve → paginate
  (4)          (6)               (7)            (8)          (9)      (11)
```

`ast-assemble` (stage 8) is what produces *"`ast.json` (canonical) + integrity hash"*, and `resolve` (stage 9) produces `doc.effective.json` — **which is what `paginate` consumes.** The implementation instead has `extract` emit `typescript-html/1` straight into `paginate`, and hangs `structure` off `acquire` as a dead end.

That inversion is *why* the gate is bypassable. It is not an ordering bug to be patched with a sort key.

**Change.** Declare the real chain. Minimum viable subset of the canonical stages, keeping current module names where they already match:

| Stage | inputs | outputs |
|---|---|---|
| `acquire` | `root_inputs=["manifest_path"]` | `raw-source/1` |
| `extract` | `raw-source/1` | `typescript-html/1` |
| `structure-rules` | `typescript-html/1` | `ast-draft/1` |
| `ast-assemble` | `ast-draft/1` + `raw-source/1` | `ast/1` + `integrity-report/1` |
| `resolve` | `ast/1` + `overrides/1` | `doc-effective/1` |
| `design-compile` | `designspec/1` | `text/css` |
| `paginate` | **`doc-effective/1`** + `text/css` | `raw-pdf/1` + `pagemap/1` |
| `finish` | `raw-pdf/1` + `profile/1` | `pdfx/1` |
| `preflight` | `pdfx/1` + `profile/1` | `preflight/1` **GATE** |
| `package` | `preflight/1` + `pdfx/1` + `manifest`-producing upstream | `build-report/1` |

**The load-bearing property:** `paginate` now consumes `doc-effective/1`, which is reachable only through `ast-assemble`. **You cannot paginate a book whose integrity gate did not run.** The gate becomes unbypassable by data dependency rather than by convention — D2, and the reason this fix is a rewire and not a reorder.

Note `ast-assemble` takes **both** `ast-draft/1` and `raw-source/1`. That is what makes F2.2 possible: the stage that asserts the invariant must hold both sides of it.

Also fix the executor's silent mis-wiring: [tracer_bullet.py:121-124](../tracer_bullet.py:121) falls back to *the first declared output schema* when an artifact `kind` matches no output key. That assigns artifacts to wrong schema ids silently — a D8-banned silent fallback. Make it a hard `StageError(ENGINE_BUG)`.

**Acceptance.** `check_integrity() == []`. Topological order places `structure-rules` and `ast-assemble` before `paginate`, and `preflight` before `package`. A test asserts `order.index("ast-assemble") < order.index("paginate")` — ordering is now a property, not an observation.

### F2.2 — Make the integrity gate compare AST against source *(finding 1)*

**Root cause.** [structure_stage.py:88](../stages/structure_stage.py:88) reads `ast["integrityHash"]` — a value inside the artifact under test — and its `source` parameter defaults to the AST's own path. The source document is never opened. Additionally [line 92](../stages/structure_stage.py:92) reads `if declared and declared != expected`, so an absent hash skips the comparison and the stage reports `"passed": True`, `integrity_ok=1.0`.

[BUILD_PLAN §3.6](BUILD_PLAN.md): `assert normalize(text_stream(ast)) == normalize(text_stream(source))`. Target: **0, always. Hard fail. No flag, no override, no config.**

**Change.** Move the gate into `ast-assemble`, which by F2.1 holds both the draft AST and the original source, and compare the two text streams:

```python
def ast_assemble(ctx, ast_draft: str, raw_source: str) -> StageResult:
    ast_text    = _normalize(_text_stream_ast(json.loads(read(ast_draft))))
    source_text = _normalize(_text_stream_source(read(raw_source)))

    if ast_text != source_text:
        raise StageError(
            kind=ErrorKind.ENGINE_BUG,        # our fault, not the user's
            message="Text integrity violation: AST text stream differs from source",
            diagnostics=[_first_divergence(ast_text, source_text)],
        )
```

Four sub-changes, each a plan requirement:

1. **Compare against source, not self.** `integrityHash` becomes an *output* of this stage, never an input to its own check. Downstream stages may verify against it; the gate may not.
2. **Absent is fatal.** No `if declared and ...`. There is no path through this function that returns success without an executed comparison.
3. **Real normalizer.** NFC + whitespace collapse + smart/straight quote folding per §3.6, and the normalizer is itself property-tested (§3.6 explicitly requires this). Currently `_normalize` is `re.sub(r'\s+',' ')` only — and worse, the hash is taken over raw `all_text` while `normalized` is computed and discarded, so normalization is decorative.
4. **Actionable diagnostic.** Report the *first divergence* with a `sourceRef`, not a hash mismatch. "Hashes differ" is unactionable for a 300-page book; §3.15 requires `bad_input`/`engine_bug` errors carry an actionable message.

**Acceptance.** F1.3's three tests pass. A mutation test — delete one paragraph from a corpus AST — fails the build. Add that mutation as a permanent fixture.

### F2.3 — Put preflight in the pipeline *(finding 3)*

**Root cause.** [tracer_bullet.py:177-183](../tracer_bullet.py:177) imports 7 stage modules; `prepress_stages` (holding `preflight`, `cover`, `finish-gs`) is not among them. The run prints `TRACER BULLET PASSED` for a build that never touched the gate.

**Change.**
- Import all stage modules; let the DAG decide execution, not the import list. An import list that silently determines pipeline membership is a second, undeclared DAG.
- `package` takes `preflight/1` as a declared input, so a build cannot be packaged without a preflight verdict.
- `preflight` with `severity=critical` raises `StageError(POLICY_VIOLATION)`. Per [§3.10](BUILD_PLAN.md), **no override flag is added** — verified absent today, and it stays absent.
- The tracer's final banner asserts the preflight verdict exists and is green. "Passed" must mean the gates ran.

**Acceptance.** Tracer bullet output lists `preflight` in the executed order. A fixture PDF with a known violation fails the build with `policy_violation`.

### F2.4 — No success reports from stub paths *(finding 14)*

**Root cause.** [finish_stage.py:57-63](../stages/finish_stage.py:57) emits `"status": "passed"` with `"profile_applied": "none (tracer bullet)"` when Ghostscript is absent, and registers the unconverted input bytes as a `finished-pdf` with `media_type="application/pdf"` — even though the default input is a `.ast.json`. D8 bans *"silent fallbacks that downgrade quality without telling the user."*

**Change.** Absent engine ⇒ `StageError(ErrorKind.INFRA, "ghostscript not available")`. A stub may refuse; it may not certify. If a rendererless dev loop is wanted, gate it on an explicit `ctx.allow_stub_engines` that the API can never set — not on `shutil.which` returning `None`.

Same pattern in [paginate_stage.py](../stages/paginate_stage.py): "No PDF renderer available — producing HTML report instead" then reporting `page_count=4` from a non-paginated HTML file. Page counts from a stub feed the spine-width calculation ([ARCHITECTURE §1.3](ARCHITECTURE.md)) and must not exist.

**Acceptance.** With no engines installed, the tracer bullet **fails** with `infra`, naming the missing engine. Green-with-no-engines is the bug.

---

## F3 — Schema and agent invariants (2 days)

### F3.1 — Remove the free-text field; add the lint rule *(finding 10)*

**Root cause.** `schemas/classification/classification.schema.json` → `suggestions.runningHeadText: {"type":"string","maxLength":64}`. [§3.16](BUILD_PLAN.md) states the structure schema has **no free-text field** — *"no `text`, `note`, or `reasoning` field exists"* — and that *"a lint rule enforces 'no string field except `node_id` in any structure-route schema.'"* Neither holds: the field exists and the lint rule does not.

64 characters is still model-emitted prose in the one schema whose invariant the plan calls absolute. The plan's own resolution is explicit: text-generating routes live in `services/alttext` *"so this invariant stays absolute here."*

**Change.**
- Delete `runningHeadText` from the classification schema. If running-head text must be model-suggested, it goes through `services/alttext` (or a sibling text-emitting service), not the structure route.
- `tools/lint_schemas.py`: for every schema on a structure route, assert no `string`-typed property except an allowlist (`sourceRef`, `schema`, and `modelInfo` provenance fields, which our code writes rather than the model). Fail CI on violation.
- Keep the allowlist **explicit and short**. A pattern-based exemption ("fields ending in `Id`") is how this invariant erodes.

**Acceptance.** Lint fails on the current schema, passes after removal. A test adds a `notes: string` field to a copy of the schema and asserts the linter catches it.

### F3.2 — Bound `value` in the agent-proposal schema *(finding 11)*

**Root cause.** `schemas/agent-proposal/agent-proposal.schema.json` → `"value": {}` — accepts any type, unbounded depth, unbounded string length, sitting beside `additionalProperties: false` everywhere else. [§3.1](BUILD_PLAN.md) requires capping array lengths and string sizes in-schema *"so a malicious AST can't be a memory bomb"*; this is both a memory-bomb vector and a prose channel into the override layer.

**Change.** Replace with a closed union of what the declared proposal types actually need — a label enum, a bounded string for attribute values, an integer, a boolean. If `suggest_title` genuinely needs free text, that is a text-emitting route and belongs with F3.1's reasoning, not in an untyped `value`.

**Acceptance.** Schema rejects a 1 MB string and a 100-deep nested object. Round-trip tests still pass for every legitimate proposal type.

### F3.3 — Land the adversarial agent tests *(finding 12)*

F1.4's three tests go green here. Enforcement, not just assertion:

- **AST writes:** the action space is `OverrideOp` types only. The agent's tool surface must have no AST-writing tool at all — enforced by the `ToolRegistry`, so the test verifies a structural absence rather than a rejected attempt.
- **Subagent cap and task budget:** already modelled by `TaskBudget`; wire the cap into the runtime and assert exhaustion halts.

**Acceptance.** All three pass. Per [§7](BUILD_PLAN.md) DoD, a zero-AST-write test exists for every registered agent, not one shared test.

---

## F4 — Honesty and completion (3 days)

### F4.1 — Resolve the renderer declaration *(finding 8)*

**Root cause.** All 8 templates and the `design-compile` default declare `preferredEngine: "typst"`. **No Typst emitter exists** — only `_emit_css`. `paginate` never reads `preferredEngine`; it picks weasyprint-or-playwright by availability. This appears to be the prior audit's nit ("default says chrome, plan says Typst — change it") applied as a one-line edit, which produced a declaration that contradicts behavior.

That fix was wrong on the substance too. [BUILD_PLAN §5.1 P0](BUILD_PLAN.md) and [§5.3](BUILD_PLAN.md) make O1 a **measured decision gate**: port two templates to both emitters, measure SSIM, line-break agreement, wall-clock, peak RSS, *then* set the default. Editing a default is not running the gate.

**Change — pick one, explicitly, and record it:**

- **(a) Honest interim (½ day, recommended now).** Set `preferredEngine: "chrome-pagedjs"` to match what actually runs, and make `paginate` *dispatch* on `preferredEngine`, raising `StageError(BAD_INPUT)` for an engine with no adapter. Declaration and behavior agree; the O1 gate stays open and visible.
- **(b) Run the gate (1–2 wks, per plan).** Implement `emit_typst()`, port the two templates, measure, decide on data, set the default to the winner.

Do **(a) now regardless.** A DesignSpec that names an engine the pipeline ignores is worse than either engine choice, because it makes the cross-emitter agreement gate (§3.8) — which is what keeps two emitters honest — untestable.

**Acceptance.** `preferredEngine` values match implemented adapters. A test asserts every `preferredEngine` in `templates/` has a registered adapter. Unknown engine ⇒ `bad_input`, never a silent fallback.

### F4.2 — Complete O4 *(finding 9)*

**Root cause.** `FileSizeBudget` is implemented and correct — that is the half that kills the vendor-rejection class. The **two-artifact policy is absent**: [finish_stage.py](../stages/finish_stage.py) emits one PDF. [BUILD_PLAN §3.10](BUILD_PLAN.md): *"Never emit one compromise PDF."*

**Change.** `finish` emits both, from the same build node:

| Artifact | Settings | Consumer |
|---|---|---|
| **Press** | no downsampling, fonts embedded + subset, PDF/X-1a, OutputIntent, ICC | vendor upload |
| **Proof** | 150 dpi, linearized, subset fonts, RGB, watermarked | the human, in a browser |

Same node matters: it is what prevents a proof going stale against its press file — the failure mode of every "generate a preview later" design.

**Acceptance.** Two artifacts per build. Proof ≤ 40 % of press size (§0 target). Preflight runs against the **press** artifact.

### F4.3 — Add `placement` to the stage declaration *(finding 13)*

**Root cause.** v2.1 [§3.20](BUILD_PLAN.md)/[§4.6](BUILD_PLAN.md) add `placement` beside `queue` and `memory_budget_mb`, so *"a stage cannot be scheduled onto interruptible capacity unless its declaration says its interruption semantics hold."* The field is absent.

**Change.** `placement: Literal["arm-spot","on-demand","external"] = "on-demand"` on `@stage` and `StageDeclaration`. Default `on-demand` — a stage must *opt in* to interruptible capacity. Defaulting to spot would make a forgotten declaration a correctness risk.

**Acceptance.** Field present, defaulted safely, surfaced in the registry. No scheduler work — that is P5 (O5), and this is only the declaration it will read.

---

## F5 — Correctly deferred, explicitly gated

These are **not defects**. They were honestly reported as unimplemented and are correctly sequenced later. Listed so they are not mistaken for oversights, with the gate each waits on.

| Item | Waits on | Plan ref |
|---|---|---|
| O1 Typst emitter + measured decision | F4.1(a) first; then the P0 gate proper | §5.1 P0, §5.3 |
| O3 chapter-parallel + chapter cache | O1 decision (scope depends on it) | §3.9, §5.3 |
| O5 ARM64 spike → Spot fleet | F4.3 declaration; spike in P1 | §4.6, §5.3 |
| Cache storage backend (`CacheStore`) | key computation is done; storage is P0/P5 work | §3.3 |
| Real LLM integration | gateway shape is correct; wiring is P2 | §3.16 |
| Per-module READMEs + threat models | every module, per DoD | §7 |
| Streaming in extract/paginate (D4) | 900-page fixture will force it | §0, D4 |

**One caveat on the cache.** F2.1 makes the DAG real, which makes cache keys meaningful. Until `CacheStore` exists, every build is cold — so the §0 warm-rebuild and cache-hit-ratio targets are unmeasurable, not merely unmet. Do not report against them before then.

---

## 3. Sequencing and effort

```
F0  test discovery + CI                 ½ day   ── unblocks verification of everything
F1  detectors (all failing)             2 days  ── 14 findings become failing assertions
F2  gates + DAG rewire                  4 days  ── the critical three
F3  schema + agent invariants           2 days
F4  honesty + completion                3 days
                                       ─────────
                                       ~12 days (1 engineer)
```

**Why this order.** F0 first because a suite that collects nothing makes every later claim unverifiable. F1 before F2 because the DAG checker *is* the rewire's acceptance test, and because a finding without a failing test cannot be shown to stay fixed. F2 before F3/F4 because the three critical findings are the ones where current state is actively misleading — a build that reports `passed` without running its gates is worse than one that reports `failed`.

**Parallelizable:** F3 and F4 are independent of each other and of F2 once F1 lands. With two engineers: F0+F1+F2 on one, F3+F4 on the other, ~7 days.

**Not on the critical path:** everything in F5.

---

## 4. Non-goals

- **No rewrite.** Module boundaries, the error taxonomy, the sandbox, `pagescan`, the learning subsystem, and the writers are sound. This plan touches the DAG wiring, three stage bodies, two schemas, and adds CI.
- **No new abstractions.** Every fix uses machinery that already exists — the stage registry, `StageError`, the `@stage` decorator, `derive_contract_tests`. Finding 5 is fixed by *calling* a function that was already written.
- **No filling in of honest stubs** beyond making them refuse instead of certify (F2.4). A stub that fails loudly is finished work; a stub that reports success is a defect.
- **No re-litigating O1.** F4.1(a) makes the declaration honest without pre-empting the measurement.

---

## 5. Risk

| Risk | Mitigation |
|---|---|
| **F2.1 rewire breaks the green tracer bullet** | Expected and correct — it currently passes without running its gates. F1's detectors define the new bar. Land the rewire and the fixture updates together. |
| Real fixtures don't exist for the new chain (`ast-draft/1`, `doc-effective/1`) | F1.2 surfaces this as failing registration rather than silent absence. Hand-write them — [§5.2](BUILD_PLAN.md) notes 20 typescript fixtures is a day's work. |
| Integrity gate fails on legitimate manuscripts once it actually compares | That is the gate working. Divergences will be normalizer gaps (entities, soft hyphens, footnote markers). Fix the normalizer, property-test each case, never relax the assertion. |
| F2.4 makes local dev harder with no engines installed | Explicit `allow_stub_engines` dev flag the API cannot set. Inconvenience is the correct cost of a gate that can fail. |
| CI reveals more violations than these 14 | Good — that is the point of F0.2. Triage against this plan's severity scheme; the enforcement layer is what stops the next fourteen. |

---

## 6. Definition of done

- [ ] `pytest` from the repo root collects ≥ 221 tests with no arguments
- [ ] CI runs and is green; every "CI-enforced" gate in BUILD_PLAN has a job
- [ ] `check_integrity()` returns `[]`; unsatisfiable inputs, orphans, cycles, ambiguous producers, and non-schema inputs all fail CI
- [ ] Generated contract tests execute for every registered stage; a missing fixture set fails registration
- [ ] **Deleting one paragraph from a manuscript fails the build** — the mutation fixture is permanent
- [ ] No path through the integrity gate returns success without an executed AST-vs-source comparison
- [ ] `order.index("ast-assemble") < order.index("paginate")` asserted as a property
- [ ] `preflight` executes in every build; `package` cannot run without a verdict; no override flag exists
- [ ] No stage reports success from a stub path
- [ ] Zero string-typed fields in structure-route schemas beyond the explicit allowlist; linter enforces it
- [ ] `value` is a closed, bounded union
- [ ] Zero-AST-write, subagent-cap, and task-budget tests exist per agent
- [ ] Every `preferredEngine` names an implemented adapter
- [ ] Press and proof artifacts emitted per build; proof ≤ 40 % of press
- [ ] `placement` declared on every stage, defaulting to `on-demand`

---

## 7. The one-line summary

The implementation built the pipeline and the registry, then left out the half that makes them enforceable. **Twelve days of work, and the single highest-value line in it is the data-dependency edge that makes `paginate` unreachable without the integrity gate** — because that converts the project's most important invariant from a check somebody remembered to write into a property of the graph.
