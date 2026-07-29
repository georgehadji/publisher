# Publisher — Agent Design: Quality, Workflow, and Learning

How an agent raises output quality, collapses the user's workflow, and gets better over time — without breaking reproducibility or touching the author's prose.

Companion to [ARCHITECTURE.md](ARCHITECTURE.md) · [LLM_STRATEGY.md](LLM_STRATEGY.md) · [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

---

## 0. The trick that makes an agent safe here

A reproducible build cannot contain a nondeterministic agent. But it can contain the **frozen output** of one.

```
agent proposes  →  OverrideSet / DesignSpec patch  →  human approves  →  artifact (CAS)
   nondeterministic          deterministic data              gate            build input
```

The agent's *proposals* vary run to run. Once accepted they are override ops — ordinary, hashed, cache-keyed build inputs. Rebuild in 2029 replays the accepted ops, not the agent.

Three consequences, and they define everything below:

1. **The agent's action space is the override log and the DesignSpec — never the AST, never the PDF, never a preflight verdict.** The override layer already exists as the sanctioned mutation channel; the agent is just another actor in it, subject to the same rebase, audit, and undo.
2. **Every agent action is attributable and reversible** for free. `actor: "agent:compositor@v7"` sits next to `actor: "user:8812"` in the same log.
3. **The agent runs at gate boundaries**, never inside a deterministic stage. Same rule as every other LLM call.

---

## 1. Where an agent raises quality

Deterministic code already handles everything expressible as geometry or a rule. The agent's territory is what's left: **judgment on ambiguous structure, and visual defects that have no closed-form definition.**

### 1.1 Calibration first — what the evidence actually says

The LED benchmark (layout error detection for paginated documents) finds VLMs at **moderate accuracy**, weak on subtle spacing and alignment violations, and weakest when the error requires contextual judgment rather than obvious malformation. Human design judgment remains necessary for complex aesthetic violations.

That is not an argument against using a VLM here — it's an argument for **assigning it the right job**:

| Defect class | Right detector | Why |
|---|---|---|
| Widows, orphans, runts, hyphen ladders, short chapter ends, baseline drift, ink coverage, box geometry | **Deterministic scan of the pagemap** | You have exact coordinates. A model is strictly worse — slower, costlier, less accurate, non-reproducible |
| Rivers | **Rasterized column-density scan** | Signal processing problem, not a perception problem |
| Chapter-opener composition, drop-cap optical collision, figure placement that is legal but ugly, table break that is legal but confusing, page that "reads unbalanced" | **VLM, on a crop, as a second opinion** | No closed-form definition. This is the residue |
| Whether the whole template is right for the book | **Human typographer** | Unchanged. See PRODUCTION_READINESS §2.4 |

Geometry first, model second, human third. A VLM used where geometry works is a regression; geometry used where judgment is needed is the current state of the art in every competitor.

### 1.2 The vision loop that actually works

The single highest-leverage implementation detail: **give the agent tools to crop and visually verify its own work.** On current Opus, iterative analyze-crop-verify is a markedly more cost-effective quality lever than raising thinking budget.

```
scan pagemap (script)  →  candidate defects with bboxes
      ↓
crop(page=143, bbox)   →  image of just the suspect region
      ↓
look                   →  "the drop cap's shoulder collides with line 2's ascender"
      ↓
propose_adjustment     →  DesignSpec patch, scoped to this spread
      ↓
recompose + re-crop    →  verify the fix, or revert
```

Crop, don't render whole pages at full resolution — the token cost is per-pixel and most of the page is irrelevant to the finding.

### 1.3 Agent roles

Each is a separate agent with a narrow tool surface, its own eval set, and its own gate. Do not build one general "book agent."

| Agent | Trigger | Tools | Action space | Gate |
|---|---|---|---|---|
| **Structure Wrangler** | rules confidence low across the doc | `query_nodes`, `sample_text`, `preview_structure`, `propose_override` | OverrideSet ops | Human review UI; agent proposals pre-populate it |
| **Compositor** | after `paginate`, when defects survive the deterministic fixpoint | `scan_pagemap` (code exec), `render_range`, `crop`, `propose_adjustment` | DesignSpec patches, scoped to a spread | Preflight + raster diff + human |
| **Preflight Explainer** | preflight produces findings | read-only over `preflight.json` + `pagemap` | text report only | None needed — cannot change the build |
| **Manuscript Doctor** | on upload, pre-ingest | read-only over `typescript.html` | advisory text | None |
| **Vendor Watcher** *(internal)* | nightly | web fetch, read `profiles/*.yaml` | opens a PR | Human review of the PR |
| **Support Triage** *(internal)* | new ticket | repro bundle, build manifest, logs | draft reply | Human sends |
| **Corpus Synthesizer** *(internal)* | on demand | write to `corpus/` | synthetic manuscripts | Reviewed like test data |

Tool-surface rule: promote an action from generic code execution to a **dedicated tool** when you need to gate it, render it in the UI, audit it, or parallelize it. `propose_override` and `crop` are dedicated tools for exactly those reasons. Data munging over a pagemap is not — that's code execution.

### 1.4 Programmatic tool calling is not optional here

A 400-page book's pagemap is thousands of line boxes. An agent that inspects it page by page is 400 round trips, most of the tokens never used again, and a cost model that doesn't close.

Instead, the agent **writes a script that runs inside code execution and calls the scan tools from within the loop**. Intermediate results stay in the sandbox; only the defect summary enters context. One turn, not four hundred.

This is the difference between a demo and a product for any agent operating over paginated documents.

### 1.5 Cost and pacing

- **Advisor pattern**: cheap executor model doing the grind, expensive advisor consulted for the plan. The advisor must be at least as capable as the executor.
- **Task budget** per repair session so the agent paces itself and wraps up gracefully rather than being cut off mid-fix (minimum 20k tokens).
- **Context editing / compaction** for long sessions — clear stale tool results rather than carrying a hundred crops in context.
- **Cap subagents explicitly.** Current Opus delegates readily; an uncapped Compositor will spawn a subagent per spread and multiply cost for no gain.
- **Effort low** for mechanical scanning, higher only for the planning step.

---

## 2. Where an agent improves the workflow

Quality and workflow are different products. The workflow wins are larger and cheaper.

**Collapse gate 1.** Today: user confirms 87 chapters. With the Structure Wrangler: user resolves 3 ambiguities, each pre-diagnosed with a rationale and a jump link. Review drops from ~40 minutes to ~4. This is the single biggest funnel improvement available — the structure-review step is where you will lose people.

**Make the revision loop legible.** Manuscript v2 arrives after copyedit. The agent produces an author-readable diff: *"3 chapters added, chapter 7 retitled, 2 of your earlier structure decisions no longer have a home — here they are."* The rebase already works mechanically; the agent makes it comprehensible.

**Explain, don't just do.** Every proposal ships with a one-line rationale and a before/after crop. This is what converts an automated tool from "scary black box that touched my novel" into something a customer trusts. It's also the mechanism by which the user teaches the system (§3).

**Advise upstream.** The Manuscript Doctor's report — *"340 paragraphs use manual tabs instead of styles; fixing that in Word cuts your review from 40 minutes to 5"* — sets expectations honestly and reduces bad-input churn. Cheapest quality intervention in the system, because it moves work to where it's actually easy.

**Run asynchronously.** The agent works while the user does something else; notify on completion. Long agent turns are normal — plan the UX for minutes, not seconds, with visible progress.

**Absorb spec drift.** The Vendor Watcher turns a listed operational risk (KDP/IngramSpark change requirements silently) into a PR with a diff. Internal, unglamorous, high value.

---

## 3. How it learns

Four loops at four timescales. Only two of them are "learning" in any durable sense, and the last one is the one that matters.

```
L0  in-episode        seconds     self-verification; nothing persists
L1  working memory    hours–days  per-title / per-tenant memory files
L2  consolidation     nightly     offline review of the day's corrections → exemplars, skills
L3  distillation      weekly+     mined deterministic rules → the LLM call disappears
```

### L0 — In-episode self-verification

Compose → scan → crop → look → adjust → recompose, bounded. Not learning; self-correction. Cheap and high-yield, and the crop-and-verify loop is where most of the quality comes from.

### L1 — Working memory

The memory tool is a **filesystem the agent reads and writes with ordinary file tools** — folders and text files, navigated with bash and grep. Because the backend is yours, you control scoping, retention, and audit.

Scope it explicitly:

```
/memories/
  title/<title-id>/     this author uses ~~~ as a scene break; verse blocks in ch.4, 9, 12
  tenant/<tenant-id>/   house style: chapter openers verso, oldstyle figures in body
  global/               (read-only to the agent — promoted knowledge only)
```

Rules: never write author prose verbatim into memory beyond the minimum needed to identify a pattern; never write credentials; per-tenant isolation is a hard boundary, not a convention; memory is covered by the same deletion guarantee as everything else.

The production-standard shape is tiered — a small always-in-context core, a retrieval layer behind it, and an **explicit forgetting policy**. The forgetting policy is the part teams skip and the reason agent memory rots.

### L2 — Nightly consolidation

An offline pass over the day's corrections: cluster them, identify recurring failure modes, restructure memory, propose new few-shot exemplars and new skills. Anthropic ships this pattern as agents reviewing past sessions, identifying recurring patterns and failure modes, and restructuring their own memory without waiting for a human.

The pipeline is the SkillForge shape, and it is worth copying wholesale because it includes the parts people forget:

```
trajectories + corrections
   → extract recurring patterns
   → formalize as a skill / exemplar / candidate rule
   → VALIDATE against held-out cases
   → deploy
   → MONITOR success rate in production
   → RETIRE when it degrades or conflicts
```

Validation and retirement are not optional garnish — SkillForge's reported failure modes are precisely what happens without them: **skill conflict** (new capability contradicts an existing one), **overfitting** to a handful of samples, **degradation** as the input distribution shifts, and **validation gaps** on rare cases. Human oversight remains necessary to stop accumulated skill errors from cascading.

Consolidation runs **offline and gated**. Never live self-modification of a system that produces print-ready files.

### L3 — Distillation to determinism

The endgame, and the part that distinguishes this from a generic agent product:

> **The agent's job is to make itself unnecessary.**

Every recurring correction pattern that can be expressed as a deterministic rule should become one. Each mined rule permanently removes a class of LLM calls — cheaper, faster, reproducible, auditable, and testable in CI. Most agent products try to expand agent surface area; this one should be shrinking it every month, with the agent's remaining territory being genuinely irreducible judgment.

### 3.1 The promotion ladder

```
episode scratchpad          discarded at end of run
  → title memory            retained with the title
    → tenant style profile  house style, per publisher
      → global exemplar     enters the cached system prefix (all tenants)
        → deterministic rule  code, versioned, CI-gated, LLM call deleted
```

Each promotion requires evidence and validation. Two gates matter especially:

- **tenant → global** requires a **cross-tenant generalization check**. Publisher A's house style becoming everyone's default is simultaneously a quality bug and a confidentiality incident.
- **global → rule** requires the pattern to hold on the frozen golden corpus with no regressions.

Thresholds, tuned but never zero: promote only on ≥N occurrences, across ≥K distinct titles, from ≥3 distinct tenants.

### 3.2 What "feedback" actually is

Learning consumes signals of very different strength. The 2026 work on continual learning from user-feedback logs frames this correctly: the interesting signal is **implicit** — which outputs users accepted, corrected, or ignored.

| Signal | Source | Strength |
|---|---|---|
| Explicit override (user reclassified X→Y) | review UI | **Strongest** — a clean label |
| Rejected agent proposal | proposals UI | Strong negative |
| Re-edit after accepting | override log | Strong negative, delayed |
| Preflight failure after an agent adjustment | preflight gate | Hard negative |
| Vendor rejection on a green build | delivery | Hardest negative; rare and expensive — weight accordingly |
| Typographer template review | internal | Gold label |
| Support ticket | support | Negative, unlabeled — needs triage before use |
| **Silent accept** | build log | **Weak, and a trap** |

**Silent accept is the trap.** Absence of correction is not evidence of correctness — most indie authors do not know what good typesetting looks like, which is why they are buying this. Only count a silent accept as positive **if the user actually viewed the relevant page raster**; instrument that, and treat un-viewed pages as no signal rather than as approval.

### 3.3 Evaluation discipline — the part that makes learning safe

- **Frozen golden corpus, never learned from.** Versioned, held separate, the only thing that gates promotion.
- **Held-out split** from the override log for validation, rotated.
- **Multi-judge consensus for anything subjective.** Reported industry numbers put multi-judge consensus at 97–98% accuracy against ~80–85% for a single judge or a single human pass alone. Use **distinct lenses** (typographic correctness / reading experience / spec compliance), not three copies of the same prompt.
- **Judges are production code and need calibration.** Run the judge against an expert-labeled ground-truth set, measure agreement dimension by dimension, adjudicate disagreements, and feed the outcome back into the rubric. The Judge Reliability Harness line of work tests for exactly the failure modes that bite: label-flip accuracy, invariance to formatting and paraphrase, verbosity bias, stochastic stability, and calibration across ordinal scales. Build those tests.
- **Every promotion is a PR** with before/after metrics on the corpus. CI blocks regressions.
- **Ratchet, never regress.** A learned change that improves the aggregate but regresses any golden case is rejected pending explicit review. Aggregate-only gates let a system get better on average while getting worse on the cases that generate refunds.

### 3.4 Failure modes to design against

| Failure | Guard |
|---|---|
| Skill conflict | Namespaced skills, explicit precedence, conflict detection in validation |
| Overfitting to few samples | Occurrence + distinct-title + distinct-tenant thresholds |
| Degradation under distribution shift | Continuous per-rule precision monitoring, auto-retire below threshold |
| Validation gaps on rare inputs | Keep an "unusual manuscript" bucket permanently in the corpus |
| Cross-tenant style leakage | Generalization check at the tenant→global gate |
| **Feedback-loop poisoning** | The agent's proposals shape what users see, which shapes what they correct — a closed loop that drifts. **Hold out a random slice of manuscripts from agent assistance** to maintain an unbiased baseline. This is experimental design, and skipping it means you cannot tell improvement from drift |
| Model upgrade invalidates calibration | Treat a model change like an engine upgrade: full corpus re-run, judge re-calibration, raster diffs. Same release discipline as a Chrome bump |
| Learned artifacts break reproducibility | Exemplar-set hash and rule-set version are part of the toolchain digest and the cache key. A learned change invalidates cache **correctly**, rather than silently altering old builds |

That last row is load-bearing: learning and reproducibility only coexist if the learned state is versioned and hashed like any other input.

---

## 4. Build order

| Phase | Ship | Why here |
|---|---|---|
| **P2** (with review UI) | Structure Wrangler proposals; explicit-override capture with full provenance | The override log must exist and be clean before anything can learn from it |
| **P3** (with typography) | Preflight Explainer; deterministic pagemap scan; **crop+verify Compositor** | The deterministic scan must be right before a model gets a second opinion |
| **P4** | Manuscript Doctor; Support Triage; Vendor Watcher | Operational leverage once volume exists |
| **P5** | L1 memory (title + tenant scopes) with forgetting policy | Needs multi-session usage to be worth anything |
| **P6** | L2 nightly consolidation; judge calibration harness; holdout slice | Needs enough corrections to cluster |
| **P7+** | L3 rule distillation, continuous | Ongoing forever; the metric is *LLM calls removed per month* |

**Do not build the agent before the deterministic layer is correct.** An agent papering over a weak pagemap scanner produces plausible fixes for defects you should have detected exactly, and you will never know which is which.

---

## 5. The four that matter

1. **The agent proposes override ops; humans accept; the accepted ops are the build input.** That's how a nondeterministic agent lives inside a byte-reproducible pipeline.
2. **Geometry first, VLM second, human third.** VLMs are documented as only moderately accurate on layout errors — give them the residue that has no closed-form definition, not the work coordinates already answer.
3. **Learning is a promotion ladder ending in deleted LLM calls**, not an ever-growing prompt. Measure success as inference removed, not intelligence added.
4. **Hold out a slice from agent assistance forever.** Without an unbiased baseline you cannot distinguish a system that is learning from one that is drifting, and every metric you have will tell you the comforting version.

---

## Sources

- [LED: A Benchmark for Evaluating Layout Error Detection in Document Analysis](https://arxiv.org/pdf/2603.17265) — VLM accuracy on layout/typographic defects
- [SkillForge: Forging Domain-Specific, Self-Evolving Agent Skills](https://arxiv.org/pdf/2604.08618) — trajectory→skill pipeline, validation, retirement, failure modes
- [Anthropic memory + self-improving agent memory ("dreaming")](https://www.mindstudio.ai/blog/claude-dreaming-feature-self-improving-agent-memory) · [memory tool as a filesystem](https://s3p-studios.com/blog/anthropic-memory-tool-context-engineering-agents/) · [AI agent memory systems, 2026 engineering guide](https://jobsbyculture.com/blog/ai-agent-memory-systems-guide-2026)
- [Implications of continual learning for LLM agents](https://www.lesswrong.com/posts/qChDifwpY8znER7cW/implications-of-continual-learning-for-llm-agents) — consolidation phases, memory blocks at different update frequencies
- [Awesome lifelong LLM agents (TPAMI 2026)](https://github.com/qianlima-lab/awesome-lifelong-llm-agent) · [A Survey on the Optimization of LLM-based Agents](https://arxiv.org/pdf/2503.12434) · [Self-evolving LLM agents](https://www.emergentmind.com/topics/self-evolving-llm-based-agents)
- [Judge Reliability Harness](https://arxiv.org/pdf/2603.05399) · [Calibrating LLM-as-judge with human corrections](https://www.langchain.com/resources/llm-as-a-judge) · [LLM-as-a-judge complete guide](https://galtea.ai/blog/llm-as-a-judge-the-complete-guide) · [Bias in the Loop: auditing LLM-as-a-judge](https://arxiv.org/html/2604.16790v1)
- [Vision-language models for industrial inspection](https://friendli.ai/blog/industrial-inspection-with-vision-language-models) · [VLMs in manufacturing 2026](https://ifactoryapp.com/industries/manufacturing-plant/vision-language-models-manufacturing-2026)
- [AI in publishing workflows 2026](https://edtek.ai/kb/ai-book-publishing-tools-2026/) · [Reinventing academic book workflows with AI](https://integranxt.com/blog/from-disruption-to-direction-reinventing-academic-book-workflows-with-ai/) · [Publishing trends 2026](https://www.luminadatamatics.com/resources/blog/top-10-publishing-trends-to-watch-out-for-in-2026/)
