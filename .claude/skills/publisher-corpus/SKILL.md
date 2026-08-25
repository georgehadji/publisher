---
name: publisher-corpus
description: Map of the `corpus/` folder — the golden manuscript corpus, the synthetic manuscript generator (O8) that mimics real-world Word abuse, and the raster-diff regression harness (SSIM ≥ 0.995). Use this whenever a task touches regression testing against real books, generating synthetic messy manuscripts, raster/visual diffing, or asks "what do we test the pipeline against end to end". Read before editing anything under corpus/.
---

# `corpus/` — golden and synthetic manuscripts

Regression material for the whole pipeline, as distinct from `fixtures/` (per-stage
contract inputs).

**Why synthetic exists.** Golden-corpus licensing of real published works is on the
critical path and can fail. The synthetic generator is the hedge, and it was built in P0
so it exists *before* it is needed rather than after the gate slips.

## Files

| File | What it does |
|---|---|
| `README.md` | Corpus layout, the manuscript table, toolchain notes, licensing status (Track B). |
| `generator/generate.py` | **Synthetic Corpus Generator (O8).** Emits manuscripts that mimic real-world Word abuse: hard-coded formatting instead of styles, mixed heading levels, tracked changes, embedded EMF/WMF images, footnotes/endnotes, tables with varying colspan/rowspan. Deterministic — `index.json` records the seed (42). |
| `manuscripts/index.json` | The generated set's index: schema `corpus-index/1`, `generatedAt`, `seed`, template list, and one entry per manuscript. |
| `manuscripts/minimal-novel.ast.json` | Simple 3-chapter novel, clean styles, no complications. |
| `manuscripts/messy-novel.ast.json` | 6-chapter novel with typical hard-coded formatting. |
| `manuscripts/technical-book.ast.json` | STEM book: code blocks, equations, tables, footnotes. |
| `manuscripts/fiction-dialogue-heavy.ast.json` | Extensive dialogue, scene breaks, verse. |
| `manuscripts/memoir-illustrated.ast.json` | Embedded images, sidebars, blockquotes. |
| `manuscripts/stress-test.ast.json` | The everything-at-once case. |
| `raster-diff/compare.py` | `compute_ssim()` and the comparison harness. **Gate: page-raster SSIM vs golden ≥ 0.995 per template × profile.** |

`corpus/golden/` is referenced by the README as the home for typographer-verified golden
outputs; it is not populated yet.

## Commands

```bash
python corpus/generator/generate.py     # regenerate the synthetic set
python cli.py corpus generate           # same, through the CLI
```

## Rules that bite

- **The generator is seeded.** Regenerating with a different seed invalidates every golden
  comparison. If you change the seed, say so and regenerate the goldens deliberately.
- **`.ast.json` files here are `ast/1` instances**, not DOCX. The `raw-source/1` schema is
  AST JSON despite the name — see `services/ingest/publisher_ingest/__init__.py`.
- **Corpus ≠ fixtures.** Corpus drives end-to-end regression and reproducibility; fixtures
  drive per-stage contract tests. Do not point a `@stage(fixtures=...)` at `corpus/`.
- Real licensed manuscripts are Track B and gated on licensing; the synthetic set must stay
  able to carry the coverage alone.

## Related

`platform/reproducibility/` (nightly cold-vs-cached run over this corpus) · `fixtures/` ·
`docs/BUILD_PLAN.md` §5.0, §5.3, §8 · `docs/OPTIMIZATION.md` §O8.
