# Publisher — Cover Design

Companion to [ARCHITECTURE.md](ARCHITECTURE.md) · [LLM_STRATEGY.md](LLM_STRATEGY.md) · [BUILD_PLAN.md](BUILD_PLAN.md).

Design and generate book covers using OpenRouter-hosted image models, judged by a
cross-vendor vision panel, composited deterministically with the interior's real geometry.
Pricing, model list, and API shapes verified live against OpenRouter on 2026-07-30 —
see §7 for the correction log against earlier assumptions.

---

## 0. The one architectural correction this doc makes

[ARCHITECTURE.md §1.3](ARCHITECTURE.md#13-critical-dependency-edge-people-get-wrong) states
the cover "cannot be built before the interior converges" and treats `cover_art` as a single
root input to the `cover` stage (`stages/prepress_stages.py:90`). That conflates two things
that have different dependencies:

```
art        = f(genre, metadata, sample, brief, model, seed)   ← page-count FREE
geometry   = f(page_count, trim, bleed, paper)                 ← page-count BOUND (existing)
assembly   = f(art, geometry, DesignSpec.cover, ONIX)          ← joins them
```

Only **assembly** sits behind the interior's final page count. Cover **art** generation —
the expensive, nondeterministic, multi-model fan-out — runs in parallel with the entire
interior build, starting the moment metadata and a DesignSpec exist. This is the single
highest-leverage decision in this doc: it takes image generation off the interior's critical
path entirely, and it means `parity-pad` changing the page count never re-bills a generation.

`BUILD_PLAN.md §5.2` already names the correct shape in passing — *"cover takes a page
count as a parameter, not a real interior"* — this doc is that sentence, worked out in full.

---

## 1. Stage graph

```
[title-meta/1]  [designspec/1]  [manuscript sample, opt-in]
        │              │                    │
        └──────────────┴────────┬───────────┘
                                 ▼
                          cover-brief (1 LLM call, structured out)
                                 │
                                 ▼
                          art-brief/1  (ArtBrief — closed enums + free-text `concept`/`subject` only)
                                 │
                    ┌────────────┴────────────┐
                    ▼                          │
              cover-art (N models × M seeds,   │
              fan-out via POST /api/v1/images) │
                    │                          │
                    ▼                          │
            cover-art/1[]  +  art-provenance/1 │
                    │                          │
                    ▼                          │
              cover-judge (deterministic gates,│
              then cross-vendor vision panel)  │
                    │                          │
                    ▼                          │
              art-ranking/1                    │
                    │                          │
       ── HUMAN GATE 3 (cover-selection UI) ───┘
                    │
                    ▼
            cover-selection/1
                    │           [page_count]  [cover-geometry/1]  (existing `cover` stage output)
                    └────────────────┬──────────────────────────┘
                                     ▼
                              cover-compose  (deterministic: type, geometry, art field)
                                     │
                                     ▼
                              cover-raw-pdf/1
                                     │
                                     ▼
                              cover-preflight  (GATE — reuses services/prepress rules)
                                     │
                                     ▼
                              cover-pdf/1  (pdfx/1)
```

Six stage declarations, one existing stage modified:

| Stage | v | inputs | outputs | queue | LLM | root inputs |
|---|---|---|---|---|---|---|
| `cover-brief` | 1 | `title-meta/1`, `designspec/1`, `manuscript-sample/1?` | `art-brief/1` | `q.external` | 1× text, structured out | `title-meta`, `designspec`, `manuscript_sample` |
| `cover-art` | 1 | `art-brief/1`, `art-policy/1` | `cover-art/1`[], `art-provenance/1` | `q.external` | N× image models | `art_policy` |
| `cover-judge` | 1 | `cover-art/1`[], `art-brief/1`, `judge-policy/1` | `art-ranking/1` | `q.external` | vision panel (batch) | `judge_policy` |
| — HUMAN GATE 3 | | `art-ranking/1` + UI | `cover-selection/1` | web | — | — |
| `cover-compose` | 1 | `cover-selection/1`, `cover-geometry/1`, `designspec/1`, `title-meta/1` | `cover-raw-pdf/1` | `q.render.html` | none | — |
| `cover-preflight` | 1 | `cover-raw-pdf/1`, `profile/1` | `preflight/1` **GATE** | `q.prepress` | none | — |
| `cover` *(existing, modified)* | **2** | `page_count`, `profile/1` | `cover-geometry/1` | `q.prepress` | none | `page_count`, `profile_name` |

`cover` (`stages/prepress_stages.py:87-148`) keeps computing geometry only — it never
consumed art in the first place (the current code takes `cover_art: str` but never reads
it). Bumping it to v2 and dropping the unused `cover_art` param from its declared `inputs`
is a documentation fix that makes the DAG-integrity checker (`check_integrity()` in
`platform/stages/py/publisher_stages/__init__.py`) stop lying about a dependency that
never existed in the implementation.

---

## 2. `ArtBrief` — the semantic layer between metadata and pixels

Two tiers, on purpose, mirroring `LLM_STRATEGY.md §3`'s closed-enum discipline:

- **The brief is semantic.** One LLM call, cached, human-editable, versioned. Free text is
  confined to `concept` and `subject` — everything else is a closed enum. This is *not*
  prose about the book (LLM_STRATEGY's "never touch the author's prose" boundary is
  untouched — the brief describes cover *imagery*, never quotes or paraphrases the text).
- **The prompt is a deterministic render of brief × model dialect.** GPT-Image wants prose
  scenes; Seedream and Recraft want tag stacks; Gemini wants conversational edit
  instructions; Krea exposes a `moodboards`/`styles`/`intensity` passthrough that maps
  almost directly onto brief fields. Templating this, versioned like a stage, means adding
  a model to the panel never costs a second LLM call.

Schema: [`schemas/cover/art-brief.schema.json`](../schemas/cover/art-brief.schema.json).

```jsonc
{
  "schema": "art-brief/1",
  "concept": "A lone figure crossing a frozen lake at dusk, the ice cracking underfoot",
  "subject": "solitary human figure, back to viewer, small against the landscape",
  "composition": "rule-of-thirds",
  "palette": ["desaturated-blue", "bone-white", "charcoal"],
  "lighting": "low-key",
  "medium": "painterly-digital",
  "mood": "tense",
  "genreSignals": ["literary-thriller"],
  "typeZone": "lower-third",
  "negative": ["text", "readable-typography", "photorealistic-face", "logo"],
  "sourceRef": { "titleMetaHash": "sha256:…", "designSpecHash": "sha256:…" }
}
```

`typeZone` (`upper-third | lower-third | center-band | none`) is the field that connects the
brief to §5 — it tells every model where to leave low-detail space, and `cover-compose`
verifies contrast there before setting type.

### Manuscript sample (opt-in, per your decision)

`cover-brief` may take a manuscript excerpt to ground the brief in actual tone rather than
metadata alone. Constraints, all enforced in the stage, not left to prompt discipline:

- **Opt-in per tenant.** Off by default. The onboarding flow surfaces this as an explicit
  toggle, not a checkbox buried in ToS.
- **Sample capped at ~2,000 tokens**, taken from the opening chapter only (never
  mid-book, never dialogue-dense sections — avoids anything that reads as leaking plot).
- **ZDR-filtered models only** for this call — `provider.zdr: true` in the routing policy
  (§4). The sample is input; it is never echoed into the `ArtBrief` output, so it cannot leak
  downstream into `cover-art` prompts or the delivered artifact.
- Logged in `art-provenance/1` as a boolean (`sampleUsed: true`) — never the sample text
  itself.

---

## 3. Model catalogue — generated, not hand-maintained

Model discovery is a **scheduled job, never a request-time lookup** (`ARCHITECTURE.md`
D3/D9's reproducibility argument applies to model selection exactly as it applies to LLM
calls: resolving "the best image model" per-request makes the effective model a runtime
choice while `model_id` in the cache key stays constant).

```bash
cd services/cover && python -m publisher_cover.sync_catalogue
```

Probes `GET /api/v1/models?output_modalities=image` (pricing, Design Arena ELO) and
`GET /api/v1/images/models[/{id}/endpoints]` (per-**provider** capability), writes
[`services/cover/publisher_cover/model_catalogue.yaml`](../services/cover/publisher_cover/model_catalogue.yaml),
committed and diff-reviewed like any other pinned dependency. 38 image-output models
probed 2026-07-30, 24 usable as cover art (2:3-capable), 8 seeded.

### The billing-unit trap

Three different pricing units exist, and they invert ranking at print resolution. A 6×9in
cover at 300dpi is **4.86 MP** front-only, **10.82 MP** full wrap:

| unit | behavior | example @ full wrap |
|---|---|---|
| `image` (flat) | resolution-independent | `seedream-4.5` — **$0.04** regardless of size |
| `token` | scales with resolution/quality | `gpt-image-2` high — ~$0.125 |
| `megapixel` | scales with pixel count | `flux.2-max` — **$0.76** (19× the flat-rate model) |

**Default the `basic` and `pro` tiers to flat-rate (`image`) models at print resolution.**
Megapixel-billed models (the FLUX.2 family) are fine for web-resolution exploration and
genuinely strong on Design Arena, but route them through the `studio` tier where the cost
is deliberate, not accidental.

### Capability varies by *provider*, not just by model id

```
google/gemini-3-pro-image
  ├─ google-vertex/global      → resolution: 1K, 2K       ← no 4K
  └─ google-ai-studio/global   → resolution: 1K, 2K, 4K
```

`art_policy.py`'s dispatch unit is `(model_id, provider_slug)`, never bare `model_id`.
`cover-art` validates the requested `resolution`/`aspect_ratio` against the *chosen
provider's* enum before dispatch — a `BAD_INPUT` `StageError`, not a runtime 400 from
OpenRouter. Every dispatch sends `provider.only: [<slug>]` and `provider.allow_fallbacks:
false`; a silent failover would return different pixels under an unchanged cache key.

### Only 2:3-native models generate cover fronts

`2:3` is exactly 6×9in. Recraft's whole line (`1:1 4:3 3:4 16:9 9:16 auto`) cannot hit it —
so every Recraft model is scoped to `role: ornament_vector_svg` / `ornament_raster` in the
catalogue, never `cover_art`. This is also where the only SVG output on the platform lives
(`recraft/*-vector`), which is why ornaments, dinkus, and imprint marks route there — SVG
survives into PDF/X as vector, a raster cover front does not need to.

### Only 8 of 38 models support `seed`

`seedream-4.5` · `flux.2-pro` · `flux.2-flex` · `flux.2-max` · `flux.2-klein-4b` ·
`krea-2-large` · `krea-2-medium` · `krea-2-medium-turbo`. A seeded model is *replayable* —
regenerate the exact pixels on demand. Everything else is reproducible only because the
CAS artifact is frozen (§0's existing guarantee covers it either way, but replay is a much
better UX for "same cover, cooler palette"). `art-provenance/1` tags each artifact
`replay: seeded | artifact-only`; the default tier prefers seeded models.

---

## 4. OpenRouter image API — verified shape

Image generation is **not** `chat/completions` with a `modalities` field. It is a dedicated
endpoint, confirmed against `https://openrouter.ai/docs/features/multimodal/image-generation`:

```
POST /api/v1/images
{ model, prompt, n: 1-10, aspect_ratio: "2:3", resolution: "4K",
  quality, output_format: "png"|"jpeg"|"webp"|"svg",
  background: "transparent"|"opaque"|"auto", seed, stream,
  input_references: [{ type: "image_url", image_url: { url } }],
  provider: { only: [...], allow_fallbacks: false, options: { "<slug>": {...} } } }

→ { created, data: [{ b64_json, media_type }], usage: { prompt_tokens, completion_tokens, total_tokens, cost } }
```

- **`usage.cost` is exact USD per call** — wire it straight into cost telemetry, no price
  table to maintain for these calls specifically (the pinned catalogue still matters for
  *choosing* a model before the call).
- **Billing is all-or-nothing.** A generation "is either completed and billed in full, or it
  fails and is not billed — there is no partial or fractional image billing." Streaming
  partial-preview images carry no partial charge either. Retry on `infra` is free.
- **Read `media_type` from the response, don't assume PNG** — Recraft vector output
  returns `image/svg+xml`; other models can return jpeg/webp depending on requested
  `output_format`.
- **`input_references` has no documented style-consistency guarantee.** OpenRouter calls
  it "reference images for image-to-image"; the worked example is a style *transform*
  prompt, not a style-lock promise. Use it for series continuity and iteration, but verify
  the result — don't advertise it as guaranteed style-lock.
- **A separate, LLM-mediated image path exists and should be avoided for cover art.**
  `openai/gpt-5-image`, `gpt-5-image-mini`, and `gpt-5.4-image-2` "generate images through
  an LLM, so they don't provide access to the full set of supported parameters and may
  incur extra inference cost" (OpenRouter's own image-API announcement recommends the
  dedicated image models instead). This matches the probed catalogue exactly — those
  three ids report no `aspect_ratio` enum at all. Catalogue role for all three:
  `unsuitable_no_native_2_3`.
- **Always `provider.allow_fallbacks: false`.** Log the resolved provider into
  `art-provenance/1` — required for the cache-key discipline in §6 and for the AI-content
  disclosure in §9.

---

## 5. No image model renders title, author, or series text — ever

Generative models garble type, cannot kern, cannot hit a licensed face, cannot guarantee
the spine-safe zone, and cannot be audited against the font vault (`ARCHITECTURE.md
§2.10`). Cover typography is composed **deterministically** in `cover-compose`, extending
`DesignSpec` with a `cover` token block, through the same emitter path as the interior
(`ARCHITECTURE.md §2.7`). The image model paints the field; the compositor sets the type.

`ArtBrief.typeZone` tells the model where to leave low-detail space; `cover-compose`
measures contrast in that region before committing type placement, and fails as a
`policy_violation` (not a silent downgrade) if no submitted candidate clears the bar.

`font_inputs` (Riverflow) and `text_layout`/`style` (Recraft) passthrough params exist and
are tempting — leave them for the browsing thumbnail only, never the delivered PDF. Same
reasoning as above: unauditable against the font vault.

---

## 6. Model variants and `reasoning` — the corrected picture

**Correction against the earlier turn in this conversation:** I previously said OpenRouter
has no batch/async discount path. That was wrong. §6.3 below is the corrected version —
OpenRouter has a real Batch API and 50%-off `:batch` model variants, verified live.

### 6.1 All seven colon-suffix variants, and why cover routes use none of them

| variant | what it does (verified) | use on cover routes? |
|---|---|---|
| `:free` | free tier; "different rate limits or availability", undocumented numeric caps | No — cannot back a paid pipeline stage |
| `:nitro` | exactly `provider.sort: "throughput"` | No — re-sorts the endpoint; we want it pinned |
| `:floor` | exactly `provider.sort: "price"`; also opts out of Auto Exacto quality signals | No — same reason, worse: picks the *cheapest* endpoint each run |
| `:online` | **deprecated** — use the `openrouter:web_search` server tool instead | No — irrelevant, and deprecated |
| `:exacto` | quality-first provider sort; requires tool-calling support; explicit `provider.sort` overrides it anyway | No — `provider.only` pins harder, for free |
| `:thinking` | **"no longer supported for Anthropic models. Use the `reasoning` parameter instead."** (verbatim, reasoning-tokens page) | No, ever, for any Anthropic-routed text call in this pipeline |
| `:extended` | "larger context windows" — but the docs' own example slug (`openai/gpt-4o:extended`) resolves with an **empty `endpoints` array** — nothing is currently serving it, and no supported-model list exists anywhere | No — buys nothing, risks a dead slug |
| `:batch` | **real**, ~50% off (see §6.3) | Only on non-interactive routes: `cover-judge`, `alttext` |

The image-generation endpoint (`POST /api/v1/images`) is a separate surface from
`chat/completions` — none of these seven suffixes apply to `cover-art` dispatch at all;
they only matter for `cover-brief` and `cover-judge`, which call `chat/completions`.

### 6.2 The `reasoning` parameter — full shape

```jsonc
{
  "reasoning": {
    "effort": "high",       // one of max|xhigh|high|medium|low|minimal|none — mutually
    "max_tokens": 2000,     // exclusive with effort — send ONE, not both
    "exclude": false,       // default false; hides the trace from the response but does
                             // NOT stop generation or billing
    "enabled": true         // default inferred from presence of effort/max_tokens
  }
}
```

- **Reasoning tokens bill as output tokens**, always. `exclude: true` controls payload
  and contamination, not cost.
- **`effort` is silently coerced, not rejected.** "OpenRouter will map your requested
  effort to the nearest supported level." Never key a cache on a requested `effort` value —
  key on `max_tokens` (an exact integer) wherever the model family supports it.
  Anthropic's own normalization is documented: `budget_tokens = max(min(max_tokens *
  ratio, 128000), 1024)` with ratios max/xhigh=0.95, high=0.8, medium=0.5, low=0.2,
  minimal=0.1 — so `reasoning.max_tokens: 16000` at a 32K completion cap is the exact
  arithmetic equivalent of `effort: medium`, stated as a literal rather than a derivation.
- **`effort` values are OpenAI/Grok-native; `max_tokens` is the Anthropic/Gemini/Qwen
  knob.** Anthropic requires the completion `max_tokens` to be **strictly greater** than
  `reasoning.max_tokens` — violate this and the call hard-fails.
- **`GET /api/v1/models` exposes per-model reasoning capability**:
  `supported_efforts`, `default_effort`, `default_enabled`, `mandatory` (when true, "do
  not send `effort: none`"), `supports_max_tokens`. Read this at policy-authoring time,
  not at request time — pin the result into the routing YAML.
- **Omitting `reasoning` is not neutral.** Absence means the call inherits whatever
  `default_enabled`/`default_effort` the model reports *today*, and those move under you
  across provider migrations (the docs separately maintain migration guides for Claude
  4.6, 4.7, Opus 5, and GPT-5.6, each altering reasoning defaults). On every route in this
  doc, `reasoning` is sent explicitly — even when the explicit value is "off"
  (`{effort: "none"}` or `{max_tokens: <floor>, exclude: true}` on `mandatory: true`
  models) — so the sent config is a pinnable, hashable string instead of a moving target.
- **Multi-turn / tool-calling replay rule**: `reasoning_details` blocks returned by the
  model must be passed back **unmodified and in sequence** on the next turn. Not relevant
  to `cover-brief`/`cover-judge` — both are single-shot, no tools — but binding if the
  agent layer (`AGENT_DESIGN.md`) ever wraps a reasoning model in a tool loop.
- **Interaction with structured outputs (`response_format: json_schema`) is
  undocumented.** Neither the reasoning-tokens page nor the structured-outputs page
  mentions the other. Treat this as unverified for every route in §8 until a one-time probe
  confirms it on the exact (model, provider) pin — record the probe result in the build
  manifest, not just in a comment.

### 6.3 The Batch API — corrected

```
POST /api/beta/batches     { endpoint, model, requests: [{ custom_id, body }, ...] }
GET  /api/beta/batches/:id → { status, results: [{ custom_id, response|error }], request_counts }
```

- Base path is `/api/beta/`, not `/api/v1/` — the only such surface on the platform.
- **Field order in the request body is load-bearing**: "Serialize `endpoint` and `model`
  before `requests`" — OpenRouter stream-parses the payload. Python's `json.dumps`
  preserves dict insertion order; construct the dict in that order explicitly, don't rely on
  a library that might not.
- Supported `endpoint` values: `/v1/chat/completions`, `/v1/responses`, `/v1/messages`,
  `/v1/embeddings`. One shared shape per batch.
- **Only completion window: `24h`.** Not configurable.
- Lifecycle: `validating → in_progress → finalizing → completed`; terminal:
  `completed | failed | expired | cancelled`. Results return **inline** in the poll
  response, keyed by `custom_id` — no JSONL upload/download step.
- **Retention: batch inputs and results persist 30 days in Google Cloud Storage**, then
  auto-delete. This is separate from ZDR — ZDR "only applies to provider routing for
  inference requests," so `zdr: true` does **not** cover the batch artifacts. For
  `alttext`, which may process illustrated-book figures, this is a procurement-relevant
  fact to have ready, not a blocker.
- **The 50% discount is real but nowhere stated as a percentage** — it's derivable by
  comparing `:batch` model prices to their base twins:
  `anthropic/claude-opus-4.8` $5/$25 vs `anthropic/claude-opus-4.8:batch` $2.50/$12.50;
  `anthropic/claude-sonnet-5:batch` at $1/$5 vs $2/$10 base. Exactly half, both sides,
  every pair checked. **OpenRouter passes through provider pricing without markup** ("no
  markup on inference pricing" — FAQ), so the Anthropic batch discount reaches Publisher
  intact through OpenRouter; the only OpenRouter-side cost is the credit-purchase fee
  (5.5% Stripe / 5% crypto), charged on funding, not per token.
- **Pin the literal `:batch` slug in policy YAML.** Never resolve it from
  `GET /api/v1/models` at request time — a slug that resolves differently between two
  runs produces two different cache keys for the same manuscript, defeating "pay once per
  (manuscript × inference version)."

**What this changes from the prior turn:** a single-provider (OpenRouter-only) adapter is
now viable on cost grounds alone — the batch discount is reachable either way. Your
decision to run **multiple providers** stands, but on the correct grounds: `/api/beta/batches`
is self-described beta-maturity, Anthropic-direct exposes native features
(`prompt caching` `ttl` control, `output_config.effort`) that may not pass through
OpenRouter's `/v1/messages` batch shape identically, ZDR coverage differs per
provider-route, and provider redundancy is real value independent of any per-call
saving. §10 below specifies the adapter boundary that makes this a config choice, not an
architectural one.

---

## 7. Correction log against the prior turn in this conversation

| Claim made earlier | Status | Correction |
|---|---|---|
| "OpenRouter has no batch/async discount path" | **Wrong** | §6.3 — real Batch API + `:batch` variants, 50% off, pass-through pricing |
| "`sort=design-arena-elo-high-to-low` works" | **Wrong** | Confirmed silently ignored (returns alphabetical order). Sort client-side on `benchmarks.design_arena[].elo` after fetching `/api/v1/models` |
| "`:thinking`/`:extended` are dead weight, probably" | **Sharpened** | `:thinking` is not "probably" dead — the docs state it flatly for Anthropic models. `:extended`'s only documented example slug has zero live endpoints in the current catalog |
| "Enable reasoning on `cover-judge` — cost is negligible" | **Wrong**, reversed | No documentary support that reasoning improves aesthetic judgment (untested hypothesis, not a given), and reasoning-token cost is *not* negligible — comparable to or exceeding a single generation's cost at the docs' own 2000-token example budget. §8's judge design pins reasoning **off** by default |
| "~$0.04/image is the reference cost" | **Overstated** | That's one example `usage.cost` value from the docs' own illustrative response — real cost is per-endpoint (image/megapixel/token), see §3's billing-unit table for the actual range |

---

## 8. `cover-judge` — corrected design

Deterministic gates first (pure arithmetic — never delegate these to a model), vision
panel second, ranking only:

| check | method | gate |
|---|---|---|
| **Thumbnail legibility** (readable at 160px) | downscale, measure title-zone contrast + x-height proxy | blocking |
| Type-zone contrast | ΔL* in `ArtBrief.typeZone` region | blocking |
| CMYK gamut damage | convert, measure ΔE per region | warn |
| Total area coverage | TAC ≤ profile limit | blocking |
| Bleed / spine-safe / barcode quiet zone | geometry (reuses `services/prepress`) | blocking |
| Genre fit, composition, cliché detection | cross-vendor vision panel, pairwise | **ranking only** |

The thumbnail-legibility check is the highest-value check in the feature — it's how covers
are actually discovered at retail — and it's pure arithmetic. It never touches a model.

**Panel design, corrected per §6.2/§7:**

- **Reasoning pinned OFF by default** (`{effort: "none"}`, or `{max_tokens: <floor of
  supported_efforts>, exclude: true}` on `mandatory: true` models). Treat "does
  reasoning improve pairwise cover judging" as an A/B hypothesis to test with an explicit
  toggle, not a default — the documentation gives no basis for assuming it helps, and it
  is not free.
- **Quorum ≥ 3, cross-vendor**, pairwise not absolute scoring, no model judges its own
  generation. Every panel member is a **pinned endpoint** — exact `model_id` + exact
  `provider.only` + exact `reasoning` config — never a variant suffix, never a re-sort.
- **`pairing_seed` is pinned data**, not a runtime shuffle — A/B position assignment must
  be reproducible.
- **Runs on the Batch API** (`latency_class: batch`, `endpoint: /v1/chat/completions`) —
  this is genuinely non-interactive, unlike `cover-brief` which gates the whole fan-out.
- **Raster spec is versioned** (`long_edge_px`, `format`) — the judged artifact is
  itself derived, and its generation parameters are as load-bearing as the model config.
- Closed-enum verdict schema (`schemas/cover/cover-verdict.schema.json`):
  `{winner: "a"|"b"|"tie", schema: "cover-verdict/1"}` — no rationale field, same
  discipline as `LLM_STRATEGY.md §3`'s classification schema.

Human selects at Gate 3 from the deterministic-gate survivors, presented in panel-ranked
order. Every human pick is a labeled preference — feed it back per `LLM_STRATEGY.md §6`'s
compounding-asset pattern, applied to design taste instead of structure classification.

---

## 9. Compliance — non-negotiable, not a checkbox

- **AI-content disclosure.** Most retailers (KDP included) require it. `art-provenance/1`
  carries model id, resolved provider (from §4's `allow_fallbacks: false` discipline),
  prompt, `ArtBrief` hash, seed (if seeded), timestamp — and this record ships in the
  delivery package. If you can't answer the disclosure question, you can't publish.
- **C2PA Content Credentials** embedded in the cover PDF — same manifest data already
  being retained, cheap to attach, increasingly expected by retailers.
- **IP hygiene enforced in the schema, not the prompt.** `ArtBrief` bans living-artist
  names and specific-cover references at the validator level — "make it look like
  [famous cover]" is a lawsuit generator and will be typed by users if the door is open.
  Style *descriptors* only (`palette`, `medium`, `lighting`, `composition` — all closed
  enums already).
- **No real-person likeness without a release.** Flag at Gate 3, not after delivery.

---

## 10. Multi-provider adapter boundary

Per your decision (§6.3 supplies the corrected justification):

```python
class ImageGenPort(Protocol):
    def generate(self, request: ImageGenRequest) -> ImageGenResult: ...
    def supports(self, model_id: str, provider_slug: str) -> ProviderCapability | None: ...

class TextGenPort(Protocol):
    def complete(self, request: TextGenRequest) -> TextGenResult: ...
```

- `OpenRouterAdapter` implements both — image via `/api/v1/images`, text via
  `/v1/chat/completions` and `/api/beta/batches`.
- An `AnthropicDirectAdapter` implements `TextGenPort` only (Anthropic has no image
  generation endpoint) — used where native features matter (prompt-cache `ttl` control,
  `output_config.effort`) or where redundancy against an OpenRouter outage is worth the
  second integration.
- Route → provider is data in the routing policy (§11), not a runtime decision — the same
  discipline as everything else in this doc.
- Every adapter call logs `model_id`, `provider_slug`, `reasoning_config_hash`,
  `usage.cost`, and `cache_key` — feeding `LLM_STRATEGY.md §5`'s cost observability
  without a separate telemetry path for images.

---

## 11. Cache-key extension

`LLM_STRATEGY.md §0`'s existing key is `model_id + prompt_version + schema_version`.
Verified against the live catalogue, that is now insufficient for any OpenRouter-routed
call — a variant-qualified slug is a **distinct catalog entry** with its own pricing and its
own `supported_parameters` (proven live: `qwen/qwen-plus-2025-07-28` has no `reasoning`
in `supported_parameters`; `qwen/qwen-plus-2025-07-28:thinking` — same base model — does,
at a different price). Add, for every OpenRouter-routed route (text or image):

1. **`model_slug_requested`** — the verbatim `model` string sent, suffix included.
2. **`model_canonical_slug`** — the resolved `canonical_slug`. Variants and `~latest`
   aliases resolve automatically; the requested string alone doesn't identify the model.
3. **`model_variant`** — the suffix, explicit even when empty (`""`). Cheap, and it
   guarantees a future `:thinking` addition invalidates the key instead of silently
   reusing pre-existing artifacts.
4. **`reasoning_config_sha256`** — canonical sorted-key JSON of the whole `reasoning`
   object. Hash the object, not one field.
5. **`reasoning_effort_effective`** — read back per call, not assumed. Effort is silently
   coerced to the nearest supported level; requested ≠ effective.
6. **`request_max_tokens`** — feeds Anthropic's budget-normalization formula; changes the
   artifact even when nothing else does.
7. **`provider_pin_hash`** — `provider.only` + `allow_fallbacks` + `require_parameters`
   from policy, plus the *observed* serving provider from the response. Structured-output
   strictness and reasoning support are per-endpoint, not per-model.
8. **`json_schema_sha256` + `response_format_strict`** — hash the emitted schema bytes,
   not a hand-maintained version integer that can drift from what was actually sent.
9. **`routing_policy_version`** — this route's entire inference config now lives in
   versioned YAML (§11.1); the file's version is part of every key it produces.
10. **`sampling_params`** — every sampling param actually sent (`temperature`, `top_p`,
    `seed`), pinned explicitly rather than inherited from provider defaults.

For image routes (`cover-art`), the equivalent fields are `resolution`, `aspect_ratio`,
`n`, `seed` (when present), `input_references` content hashes, and the resolved
`provider_slug` — all already required inputs to the stage's declared schema, so they're
naturally in the existing cache-key mechanism; no new field class needed there.

**Keep out of the key:** observed `usage.cost` and `completion_tokens_details
.reasoning_tokens` — those are measurements, not config. Hashing them makes every call a
miss. Log them per `LLM_STRATEGY.md §5` and alert on `reasoning_tokens` being nonzero on a
route whose policy says `effort: none` — that's the tripwire for a silent-coercion bug,
not a cache-key field.

### 11.1 A pre-existing gap this surfaces

`LLM_STRATEGY.md §4` states "routing policy is **data, not code**." The only live route
table today is the hardcoded `_register_default_routes()` in
`services/structure/publisher_structure/inference.py:180-218` — that already violates the
stated principle, independent of this feature. `platform/routing/policy.yaml` (new,
§12) is written as the versioned-data source of truth for the *new* cover routes; migrating
`InferenceGateway` to load its existing routes from the same file is flagged here as a
follow-up, not done in this pass — it's a refactor of tested existing code with its own
review, not an add-on to shipping cover generation.

**One more pre-existing hazard this doc's research surfaced, worth a one-line fix
independent of cover design:** `LLM_STRATEGY.md §4`'s routing YAML sketch uses a bare
`effort:` key. That's Anthropic's native `output_config.effort`. On any OpenRouter-routed
path, `effort:` will be read by the next person touching the request builder as
`reasoning.effort` — a different parameter, silently turning reasoning on for every route
that has the key. `platform/routing/policy.yaml` uses `output_effort:` (Anthropic-native)
and `reasoning:` (OpenRouter, one of `omit | {effort} | {max_tokens}`) as two distinct
keys from the start, to not propagate the collision into new code.

---

## 12. Routing policy

Full YAML: [`platform/routing/policy.yaml`](../platform/routing/policy.yaml). Covers
`cover-brief`, `cover-judge`, and (as worked examples of the naming fix and cache-key
extension applied to existing routes) corrected versions of `structure.bulk`,
`structure.whole_book`, `alttext`, and `diagnostics_prose`. `cover-art` is not a
`chat/completions` route — its policy lives in `art_policy.yaml` (§3) since its shape
(model panel, resolution, aspect ratio) doesn't fit the text-route schema.

---

## 13. Cost model

Reference: 6×9in cover, flat-rate models at 4K per §3.

```
cover-brief     1 call, ~3K in / 1K out (reasoning.max_tokens=2048, excluded)   ≈ $0.05
cover-art       basic tier: seedream-4.5 × 3 seeds                             ≈ $0.12
                pro tier:   4 vendors × 3                                       ≈ $0.57–1.00
cover-judge     quorum=3, reasoning OFF, batch discount                        ≈ $0.02–0.05
cover-compose   deterministic, $0                                              $0.00
──────────────────────────────────────────────────────────────────────────────────────
basic tier, per title                                                         ≈ $0.19
pro tier, per title                                                           ≈ $0.65–1.10
```

Against `LLM_STRATEGY.md §5`'s ~$0.15/novel interior-inference cost, `basic` roughly
doubles total per-title AI spend and `pro` is ~5–7×. Still well under 1% of a $39–79 price
point. `cover-judge`'s cost dropped substantially from the prior turn's estimate now that
reasoning defaults to off (§7/§8) — the earlier ~$0.06 vision-panel estimate assumed
reasoning enabled, which turned out to have no documented justification.

---

## 14. Open items — verify before shipping

- [ ] Probe `reasoning` + `response_format: json_schema` together on each pinned
      (model, provider) pair used by `cover-brief`/`cover-judge`'s text-side calls — the
      interaction is documented nowhere on either side. Record the result in the build
      manifest.
- [ ] Confirm `provider.only: [anthropic]` + `zdr: true` leaves a live endpoint for each
      pinned Anthropic slug — ZDR coverage per provider-route isn't stated in the docs.
- [ ] Verify the `:batch` 50% discount empirically via `usage.cost` on a two-request test
      batch before treating it as policy — the docs never print the percentage in prose.
- [ ] Decide whether `InferenceGateway`'s hardcoded route table (§11.1) migrates to load
      `platform/routing/policy.yaml` in this cycle or a follow-up — currently a real gap,
      pre-existing, now more visible.
- [ ] Panel-width tiers (§3, §13) are placeholder pricing — recompute once `art_policy.py`
      is wired to live billing telemetry for a few weeks of real dispatches.
