---
name: publisher-services
description: "Map of the `services/` folder — the domain logic each stage calls into: ingest (DOCX→AST), structure (rules + LLM inference + override layer), prepress (geometry, fontvault, ghostscript, preflight), cover (art brief, model policy, image-gen port, judge), epub, idml, onix, alttext, and the agent layer. Use this whenever a task touches DOCX parsing, structure inference or classification, override/rebase logic, LLM routing, PDF/X conversion, preflight rules, book geometry, font licensing, cover art generation, or the EPUB/IDML/ONIX writers. Read before editing anything under services/."
---

# `services/` — the domain logic

Stages (`stages/`) are thin declarations. **The real work is here.** Each subfolder is an
installable Python distribution (`publisher-<name>`) with its own `pyproject.toml`. Six of
the nine have co-located `tests/`; **`alttext`, `epub` and `onix` have none** — their writers
are exercised from `services/idml/tests/test_outputs.py`, which sys.path-inserts them.

**U3 rule:** every `services/*` package must declare in its own `pyproject.toml` any
`publisher_*` package it imports. `tools/lint_service_deps.py` enforces it in CI. Without
that, cross-package imports resolve only because `Dockerfile.worker` puts everything on one
PYTHONPATH — so a packaging change breaks production silently instead of CI loudly.

## The folders

| Folder | Distribution | Called by |
|---|---|---|
| `ingest/` | `publisher-ingest` | `ingest` stage |
| `structure/` | `publisher-structure` | `ast-assemble`, `resolve`, review UI |
| `prepress/` | `publisher-prepress` | `design-compile`, `finish`, `finish-gs`, `preflight`, `cover` |
| `cover/` | `publisher-cover` | `cover-brief`, `cover-art`, `cover-judge` |
| `epub/` | `publisher-epub` | secondary output (P6) |
| `idml/` | `publisher-idml` | secondary output (P6) |
| `onix/` | `publisher-onix` | secondary output (P6) |
| `alttext/` | `publisher-alttext` | accessibility + pre-ingest advisory |
| `agents/` | `publisher-agents` | runs at gate boundaries, never inside a stage |

## Files

### `ingest/` — the front door
| File | What it does |
|---|---|
| `publisher_ingest/__init__.py` | Re-exports `docx_to_ast`, `IngestError`. |
| `publisher_ingest/docx_to_ast.py` | Real `.docx` → `ast/1`. **Carries its own post-condition**: `_assert_no_text_lost` proves every non-empty DOCX block appears in the emitted AST. The pipeline's `ast-assemble` gate cannot cover this — by the time it runs the DOCX is gone, and ingestion is precisely the step that can drop text. Legacy binary `.doc` is not supported; convert with LibreOffice first. |
| `tests/test_docx_to_ast.py` | Each rule tested here, if broken, silently loses or mangles author text. |

### `structure/` — rules first, LLM only for the ambiguous
| File | What it does |
|---|---|
| `publisher_structure/rules.py` | Deterministic first pass over typescript HTML: `parse_html`, `classify_blocks`, `find_low_confidence`, `build_ast_draft`, `compute_text_integrity`. Signals are heading tags, class names, font size/weight, centering, all-caps, numbering, ornament glyphs. |
| `publisher_structure/inference.py` | `InferenceGateway`, `InferenceRequest/Result`, `ModelTier`, `RouteConfig`, `load_routes_from_policy()`. Cascade: rules → cheap model → expensive model. **Every LLM call is frozen into a CAS artifact as data. No LLM output ever enters a deterministic stage (D9).** Routes come from `platform/routing/policy.yaml`, not from code. |
| `publisher_structure/overrides.py` | `OverrideOp`, `OverrideSet`, `apply_overrides`, `rebase_overrides`. Human decisions live *beside* the derived AST, never mutating it. Addressed by `sourceRef`; on re-ingest rebase by exact sourceRef → content-hash → fuzzy text → orphan. |
| `tests/test_rules.py`, `tests/test_inference.py`, `tests/test_overrides.py` | One per module. |

### `prepress/` — print compliance
| File | What it does |
|---|---|
| `publisher_prepress/preflight.py` | The preflight rule engine — the hard gate between build and delivery. Every check maps to a vendor profile requirement; a `severity=error` check blocks delivery. |
| `publisher_prepress/geometry.py` | Pure, deterministic book geometry: `spine_width`, `cover_dimensions`, `TrimSize`, `BleedBox`, bleed/trim/marks/gutter curve. |
| `publisher_prepress/ghostscript.py` | **Real Ghostscript invocation** — `find_binary`, `to_pdfx`, `to_proof`, `GhostscriptError`. One implementation shared by `finish` and `finish-gs` so press conversion cannot drift into two. Before this existed both stages merely *detected* `gs` on PATH and passed the file through unconverted. |
| `publisher_prepress/fontvault.py` | `validate_font_use`, `FontLicenseViolation`. `design-compile` refuses to emit a spec referencing a font whose `allowedUses` do not cover the target output — enforced in the domain layer, not the UI. |
| `tests/test_preflight.py`, `tests/test_geometry.py`, `tests/test_ghostscript.py`, `tests/test_fontvault.py`, `tests/test_profiles.py` | `test_ghostscript.py` specifically guards against a false "press-ready" claim. |

### `cover/` — art without prose contamination
| File | What it does |
|---|---|
| `publisher_cover/__init__.py` | Package surface. |
| `publisher_cover/brief.py` | `ArtBrief`, `SourceRef`, `render_prompt`, `dialect_for_model`. Two tiers on purpose: the **brief** is semantic (one cached, human-editable LLM call; free text confined to `concept`/`subject`); the **prompt** is a deterministic render of brief × model dialect, versioned like a stage — so adding a model costs zero extra LLM calls. Enums mirror `schemas/cover/art-brief.schema.json`. |
| `publisher_cover/art_policy.py` | `load_catalogue`, `capability_index`, `resolve_tier_panel`. The dispatch unit is **(model_id, provider_slug)**, never a bare model id — capability varies by provider. `provider.allow_fallbacks` is always false: a silent failover returns different pixels under an unchanged cache key. |
| `publisher_cover/model_catalogue.yaml` | Pinned model catalogue (data). |
| `publisher_cover/art_policy_tiers.yaml` | Tiered dispatch panels (data). |
| `publisher_cover/sync_catalogue.py` | Scheduled job that refreshes the catalogue from OpenRouter into pinned YAML — review the diff as a PR. **Model discovery must never be a request-time lookup**; that would make the effective model a runtime choice while `model_id` in the cache key stays constant. Run: `python -m publisher_cover.sync_catalogue`. |
| `publisher_cover/image_gen_port.py` | `ImageGenPort`, `ImageGenRequest`, `OpenRouterImageGenAdapter`, `FakeImageGenAdapter`. Calls `POST /api/v1/images`, not `chat/completions`. Reads `media_type` from the response rather than assuming PNG. |
| `publisher_cover/judge.py` | Deterministic gates first (thumbnail legibility is pure arithmetic and the highest-value check in the feature), vision panel second, ranking only. Gate functions take pre-computed raster metrics, so this package needs no image library. |
| `tests/test_brief.py`, `tests/test_art_policy.py`, `tests/test_image_gen_port.py`, `tests/test_judge.py` | `test_art_policy.py` runs against the real pinned catalogue. |

### Secondary outputs
| File | What it does |
|---|---|
| `epub/publisher_epub/__init__.py` | EPUB 3 writer with accessibility. Gate: EPUBCheck **and** ACE by DAISY. |
| `idml/publisher_idml/__init__.py` | IDML writer — a ZIP of XML (stories, spreads, master spreads, styles, resources), generatable without InDesign. Gate: opens clean in InDesign, Affinity, Scribus. |
| `onix/publisher_onix/__init__.py` | ONIX 3.0 metadata XML for vendors, retailers, libraries. |
| `idml/tests/test_outputs.py` | Covers the secondary-output writers. |

### `alttext/` and `agents/`
| File | What it does |
|---|---|
| `alttext/publisher_alttext/__init__.py` | Alt-text generation for figures. It lives in its own service so the no-prose invariant stays absolute elsewhere — but note it has **no schema of its own**: the approved free-text field is `altText` in `schemas/ast/ast.schema.json` (maxLength 2048). |
| `alttext/publisher_alttext/doctor.py` | Manuscript Doctor: pre-ingest advisory analysis over typescript HTML. Advisory text only — it cannot change a build. |
| `agents/publisher_agents/runtime.py` | `AgentRuntime`, `AgentRole`, `AgentCall`, `AgentResult`, `TaskBudget`, `ToolRegistry`, `ToolSpec`. **The agent action space is the override log + DesignSpec patches only — never the AST, the PDF, or the preflight verdict.** Every action is attributable and reversible (`actor="agent:compositor@v7"`). Agents run at gate boundaries, never inside deterministic stages. |
| `agents/publisher_agents/structure_wrangler.py` | Pre-populates the review UI with proposals when rules confidence is low. Output: OverrideSet ops. Gate: human review UI. |
| `agents/publisher_agents/compositor.py` | Crop-and-verify loop over the pagemap after `paginate`, when defects survive the deterministic fixpoint. Output: DesignSpec patches scoped to a spread. Gate: preflight + raster diff + human. |
| `agents/publisher_agents/preflight_explainer.py` | Explains preflight findings in natural language. Read-only, so no gate needed. |
| `agents/tests/test_agents.py` | Runtime + tool-surface tests. |

## Rules that bite

- **No prose into closed-enum schemas.** `tools/lint_schemas.py` fails CI on free-text fields
  — but read its actual scope before relying on it: `STRUCTURE_ROUTE_SCHEMAS` is a one-entry
  list (`schemas/classification/classification.schema.json`), and `ALLOWLIST` is a set of
  provenance *field names* (`sourceRef`, `schema`, `modelId`, `promptVersion`, …). There is no
  alttext entry — alt-text is "excepted" only because the lint never looks outside classification.
- **No LLM output in a deterministic stage.** It is frozen into a CAS artifact first.
- **Declare cross-package deps** in the service's own `pyproject.toml` (U3).
- **Model slugs are pinned, never aliased.** See `platform/routing/policy.yaml`.

## Related

`stages/` (who calls these) · `schemas/` (the contracts they produce) · `profiles/` (what
preflight measures against) · `docs/LLM_STRATEGY.md` · `docs/AGENT_DESIGN.md` ·
`docs/COVER_DESIGN.md` · `docs/BUILD_PLAN.md` §3.10–§3.18.
