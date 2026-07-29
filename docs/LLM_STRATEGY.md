# Publisher — LLM & Model-Routing Strategy

Where an LLM earns its place in this pipeline, where it must never appear, and how routing turns it from a cost line into a margin lever.

Companion to [ARCHITECTURE.md](ARCHITECTURE.md) · [BUILD_PLAN.md](BUILD_PLAN.md) · [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

Pricing and model IDs current as of 2026-07-29. Verify before committing to a cost model — Sonnet 5's introductory rate expires 2026-08-31.

---

## 0. The one architectural rule

> **An LLM call may never sit inside a deterministic stage. It runs at a gate boundary, freezes its output into a cached artifact, and the build consumes the artifact — never the model.**

This falls directly out of the reproducibility contract. A build that calls a model is not reproducible: the same input two years later gives different output. So:

```
upload → [rules] → [LLM: classify] → classification.json (CAS artifact, cache-keyed)
                                          ↓
                                    every later build reads the artifact
```

The cache key for that stage includes `model_id + prompt_version + schema_version`. Switching Haiku → Sonnet invalidates correctly. A 2029 rebuild replays the frozen artifact and produces byte-identical output. Nothing else preserves both properties at once.

Corollary: **LLM cost is paid once per (manuscript × inference version), not once per build.** A customer who tries 12 templates pays for inference once. That single design choice is worth more than any model-tier optimization.

---

## 1. Where an LLM genuinely adds value

Ranked by return, not novelty.

### Tier 1 — product-defining

| # | Use | Why an LLM is the right tool | Value |
|---|---|---|---|
| 1 | **Structure inference on low-confidence nodes** | Chapter-vs-heading vs false positive on manuscripts with no styles is genuine ambiguity. Rules get 90–95%; the tail is where products die. | Determines whether the review step takes 2 minutes or 40 |
| 2 | **Alt-text generation for images** | EAA/WCAG requires it. Authors will not write it. Vision model proposes, human approves. | Direct compliance revenue; unlocks the EU publisher segment |
| 3 | **Semantic emphasis disambiguation** | Italic = emphasis vs title-of-work vs foreign term. Changes EPUB output (`<em>` / `<cite>` / `<i lang="fr">`) and therefore screen-reader pronunciation. Pure semantics, no rule can do it. | Accessibility quality + typographic correctness |
| 4 | **Index term extraction** | Professional indexing runs $3–6/page. LLM proposes candidate terms + locators, human curates. Never auto-published. | High-margin upsell on non-fiction |
| 5 | **Design direction recommendation** | Users cannot choose well from 8 templates. Genre + a 2,000-word sample → template, type pairing, trim size, with reasoning. | Removes the biggest onboarding drop-off |
| 6 | **Diagnostic translation** | Preflight finding → plain-language cause + remedy + jump-to-page. Deterministic templates cover 80%; the tail needs language. | Support deflection; the report becomes a feature |
| 7 | **Manuscript-prep diagnostics** | "340 paragraphs use manual tabs instead of styles — here's what that costs you and how to fix it upstream." | Sets expectations honestly; reduces bad-input churn |

### Tier 2 — adjacent revenue

Metadata assist (BISAC subject codes, keywords, comp titles) · back-cover and marketing copy (opt-in, clearly labeled as generated) · running-head short-title derivation (abbreviation of the author's title, always shown for approval) · support-ticket triage against the repro bundle.

### Tier 3 — internal leverage

Synthetic messy-manuscript generation for the test corpus (real manuscripts are license-encumbered; synthetic ones that mimic actual Word abuse are cheap) · template authoring assist · rule mining from the override log (§6) · pre-screening rendered page rasters for gross defects before the human typographer reviews.

### The agentic escape hatch (Tier 1, but v2)

For the messiest 5% of manuscripts, an agent with tools — read AST, propose override ops, re-render, view page raster, iterate — can do in one pass what a user would spend an hour on manually.

The critical constraint: **the agent's action space is the override log, not the AST.** It proposes an `OverrideSet`; a human approves it. That makes every agent action auditable, reversible, and rebase-able, and it costs nothing extra architecturally — the override layer already exists as the sanctioned mutation channel. An agent that edits the AST directly is a data-loss incident waiting to happen.

---

## 2. Where an LLM must never appear

| Never | Why |
|---|---|
| **The author's prose** | The integrity gate. Non-negotiable, and §3 makes it structurally impossible rather than merely checked |
| **Pagination or line-breaking decisions** | Nondeterministic → breaks the cache, breaks reproducibility, breaks raster-diff regression testing |
| **The typography fixpoint** (widow/orphan adjustments) | Must be deterministic, monotonic, and auditable — "why is p.143 tracked tighter" needs an answer that isn't "the model felt like it" |
| **Preflight verdicts** | Must be defensible to a printer and to your own refund guarantee. A rule engine can be argued with; a model cannot |
| **Geometry, spine width, ink coverage, page parity** | Arithmetic. Using a model here is strictly worse in every dimension |

If someone proposes an LLM for any of these, the answer is no, and the reason is always one of: reproducibility, auditability, or arithmetic.

---

## 3. Prose contamination: make it unrepresentable, don't check for it

The build plan specifies a text-integrity hash gate. Keep it — but the stronger fix is upstream, in the schema:

```python
class NodeLabel(BaseModel):
    node_id: str                    # references an existing AST node
    label: Literal[                 # closed enum — no free text anywhere
        "chapter_title", "part_title", "heading_2", "heading_3",
        "body", "chapter_opening", "epigraph", "blockquote",
        "verse", "scene_break", "dinkus", "caption", "footnote",
        "front_matter", "back_matter", "false_positive",
    ]
    confidence: float               # 0.0–1.0
    # deliberately: no `text`, no `note`, no `reasoning` field

class ClassificationResult(BaseModel):
    labels: list[NodeLabel]
```

Sent as a structured output with `additionalProperties: false` and every field `required`:

```python
response = client.messages.parse(
    model="claude-haiku-4-5",
    max_tokens=4096,
    system=[{"type": "text", "text": TAXONOMY_AND_EXEMPLARS,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
    messages=[{"role": "user", "content": batch_payload}],
    output_format=ClassificationResult,
)
if response.stop_reason == "refusal":
    raise ExternalLimit.refusal(response.stop_details)
labels = response.parsed_output.labels
```

The model **cannot** emit prose, because there is no field to emit it into. The integrity hash then becomes a defence-in-depth assertion rather than the only line of defence.

**Alt-text is the one exception** — it legitimately produces new text. Keep it in a separate service with a separate schema so the structure service's "no text fields, ever" invariant stays absolute and lintable.

Schema constraints worth knowing before designing: no recursive schemas, no numeric or string length constraints (`minimum`, `maxLength` etc. are stripped and validated client-side by the SDK), `additionalProperties: false` required on every object. First request with a new schema pays a one-time compile cost; schemas are cached 24h.

---

## 4. Routing — the dimensions that actually matter

Routing policy is **data, not code**. A table the ops team can change without a deploy.

```yaml
routes:
  structure.bulk:
    tiers: [claude-haiku-4-5, claude-sonnet-5]   # cascade
    escalate_when: confidence < 0.75
    effort: low
    latency_class: interactive
  structure.whole_book:                          # part/volume-level reasoning
    tiers: [claude-opus-5]
    effort: medium
  alttext:
    tiers: [claude-sonnet-5]
    modality: vision
    latency_class: batch                         # Batch API, 50% off
  design_recommend:
    tiers: [claude-opus-5]
    effort: medium
    latency_class: interactive
  diagnostics_prose:
    tiers: [claude-haiku-4-5]
    effort: low
```

Six dimensions, in order of how much money each moves:

**1. Difficulty cascade.** Haiku 4.5 handles bulk classification; escalate only the nodes it flags as low-confidence to Sonnet 5; reserve Opus 5 for whole-book structural reasoning where the context spans thousands of nodes. Escalate on *measured* confidence or on disagreement between two cheap samples — never blindly.

**2. Latency class.** This is the single biggest cost lever after caching. Interactive work (the user is watching the onboarding spinner) runs synchronously. Everything else — alt-text, index candidates, metadata, and **all bulk backlist conversion** — goes through the **Batch API at 50% off**. Most batches finish inside an hour; the ceiling is 24h.

**3. Privacy tier.** Publisher customers will ask, in procurement, whether unpublished manuscripts are sent to a third-party model and whether they train on it. Have a routing answer, not just a contractual one: a `no_external_llm` tenant flag that routes to rules-only (with an honest confidence banner in the UI) or to a self-hosted open model. This is a **sales requirement**, not a nicety.

**4. Modality.** Vision for alt-text, figure analysis, and any scanned-PDF ingest path. High-resolution vision costs meaningfully more per image — downsample when the fidelity isn't needed, and measure with `count_tokens` before assuming.

**5. Spend ceiling.** Per-tenant monthly budget with graceful degradation: at the ceiling, drop to rules-only and *tell the user* ("structure confidence lowered — please review chapter detection"). Never a silent quality downgrade. This is the same rule as the Adobe-API spend ceiling in the architecture.

**6. Failover.** Provider outage or sustained 429s → circuit breaker opens → rules-only with a banner. On Opus 5, also opt into server-side `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`), which re-runs a policy-declined request on a fallback model inside the same call.

**Always check `stop_reason == "refusal"` before reading content.** Manuscripts are arbitrary creative work; classification prompts are benign, but a refusal returns HTTP 200 with empty or partial content, and code that indexes `content[0]` unconditionally will crash on it.

---

## 5. Cost model (real numbers)

Reference: 300-page novel ≈ 90,000 words ≈ 120,000 tokens, ~4,500 paragraphs.

| Model | Input $/MTok | Output $/MTok | Context | Min cacheable prefix |
|---|---|---|---|---|
| Claude Opus 5 | $5.00 | $25.00 | 1M | 512 tok |
| Claude Sonnet 5 | $3.00 ($2.00 intro → 2026-08-31) | $15.00 ($10.00 intro) | 1M | 1024 tok |
| Claude Haiku 4.5 | $1.00 | $5.00 | 200K | 4096 tok |

### The naive approach, and why it's wrong

Send the whole manuscript to Opus 5: 120K input × $5/M ≈ **$0.60/book input alone**. Worse than the cost — it puts the author's entire prose in a model context for a task that only needs ~5% of it, which is exactly the thing you'll be asked about in procurement.

### The right approach

Rules first. Only low-confidence nodes reach a model, with a small context window each.

```
4,500 paragraphs → rules → ~93% high-confidence
                         → ~315 low-confidence nodes
315 nodes × ~150 tok (text excerpt + 2 neighbours + features) ≈ 47K tok
batched 40/call → ~8 calls
+ system prompt (taxonomy + exemplars) ~3,000 tok, identical for every book
```

| Path | Input cost | Output cost | Total |
|---|---|---|---|
| Haiku 4.5, all 8 calls | 47K + 24K sys = 71K × $1/M = $0.071 | ~5K × $5/M = $0.025 | **$0.096** |
| + escalate 20% to Sonnet 5 | ~14K × $2/M = $0.028 | ~1K × $10/M = $0.010 | **+$0.038** |
| **Structure inference, per book** | | | **≈ $0.13** |

Add alt-text (only for illustrated books — a novel has none): 60 images × ~2,500 vision tokens = 150K × $2/M on Sonnet 5 ≈ $0.30, or **$0.15 via the Batch API**. Metadata and diagnostics add ~$0.03.

**Total LLM cost per title: ~$0.15 for a novel, ~$0.35 for an illustrated non-fiction book, roughly half that on the batch path.** Against a $39–79 price point, LLM spend is well under 1% of revenue. It is not a cost problem — but the naive design is 5× worse for no benefit, so get it right once at the start.

### Prompt caching — the biggest single lever

The taxonomy + label definitions + few-shot exemplars are **byte-identical for every book and every tenant**. That makes it a pure-content prefix that caches globally.

- Cache reads cost ~0.1× base input. Writes cost 1.25× (5-min TTL) or 2× (1-hour TTL).
- Break-even: 2 requests on the 5-min TTL, 3 on the 1-hour TTL. With continuous traffic, use `ttl: "1h"` and the prefix is effectively always warm.
- **Haiku 4.5's minimum cacheable prefix is 4,096 tokens** — a 3,000-token system prompt silently will not cache there (no error; `cache_creation_input_tokens: 0`). Either grow the exemplar set past 4K, or accept the miss since Haiku input is cheap anyway. Opus 5 (512) and Sonnet 5 (1024) cache the same prefix without trouble.
- **Fan-out timing:** a cache entry is only readable once the first response *begins streaming*. Firing all 8 calls concurrently means all 8 pay full price. Send one, await its first token, then fire the remaining seven.
- **Silent invalidators to lint for:** any timestamp, UUID, tenant ID, or unsorted `json.dumps` in the system prefix. Verify with `usage.cache_read_input_tokens` — if it's zero across identical-prefix requests, something in the prefix is varying.

### Effort — the second lever

`output_config: {"effort": "low"}` on bulk classification. Opus 5 and Sonnet 5 both perform unusually well at `low`, and classification is not a reasoning-heavy task. Reserve `medium`/`high` for whole-book structural reasoning and design recommendation. Sweep effort on your own eval set rather than inheriting a default.

Note on Opus 5: thinking is **on by default** and counts against `max_tokens`. Leave it on at `effort: "low"` — disabling it has known failure modes and saves less than lowering effort does.

### Cost observability

Per-call: log `model_id`, `route`, `effort`, `usage.input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, and the resulting artifact hash. Roll up per tenant and per route. Alert on cache-hit-rate regressions — a dropped cache hit rate is usually a prefix bug, and it shows up as a cost spike before anyone notices the code change.

Use `client.messages.count_tokens` for estimates. Never `tiktoken` — it is OpenAI's tokenizer and undercounts Claude tokens substantially, more so on code and non-English text.

---

## 6. The compounding asset

Every human correction in the structure-review UI is a labeled example of exactly where rules + LLM got it wrong, already addressed by `sourceRef` and already stored in the override log. That corpus does three things, in increasing order of value:

1. **Regression eval set.** Precision/recall per node type, gated in CI. Model or prompt changes stop being a leap of faith.
2. **Few-shot exemplars.** Feed the highest-signal corrections back into the cached system prefix. Accuracy rises; escalation rate to the expensive tier falls.
3. **New deterministic rules.** Mine recurring correction patterns into rules. Every mined rule removes a class of LLM calls *permanently*.

The result: **unit LLM cost falls with volume while accuracy rises.** That is a genuine moat, and it costs nothing extra to build — it's an emergent property of the override-layer design already in the architecture. The only thing you must do is treat the override log as a first-class dataset from day one: retain it, version it, and never let a schema migration lose it.

---

## 7. Implementation checklist

- [ ] LLM output frozen into a CAS artifact; `model_id + prompt_version + schema_version` in the cache key
- [ ] Structured outputs with closed enums; **no free-text field** in the structure schema
- [ ] `stop_reason == "refusal"` handled before reading content, on every call
- [ ] Rules-first; only low-confidence nodes reach a model, batched ~40/call
- [ ] System prefix cached (`ttl: "1h"`), byte-stable, linted for invalidators, verified via `cache_read_input_tokens`
- [ ] Fan-out sends one call first, awaits first token, then fires the rest
- [ ] Routing policy is versioned YAML, not code
- [ ] Cascade with confidence-based escalation; no blind escalation
- [ ] Batch API for every non-interactive path (50% off) — especially backlist conversion
- [ ] Per-tenant spend ceiling with **visible** degradation to rules-only
- [ ] Circuit breaker → rules-only + honest banner; `fallbacks: "default"` on Opus 5
- [ ] `no_external_llm` tenant flag routing to rules-only or self-hosted
- [ ] Alt-text isolated in its own service so "no text fields" stays absolute in the structure service
- [ ] Agentic repair (v2) proposes `OverrideSet` ops only — never touches the AST
- [ ] Override log retained and versioned as a training/eval dataset from day one
- [ ] Per-call cost telemetry with cache-hit-rate alerting

---

## 8. The three that matter

1. **Freeze LLM output into a cached artifact.** Without this the build isn't reproducible and inference is re-paid on every render. This is the whole game.
2. **Closed-enum schemas make prose contamination structurally impossible** — stronger than any post-hoc check, and it's the answer you give in a procurement review.
3. **The override log compounds.** Volume makes the product cheaper *and* more accurate. Design for that on day one; it cannot be retrofitted from data you didn't keep.
