---
name: publisher-templates
description: Map of the `templates/` folder — the DesignSpec preset library (literary, thriller, memoir, academic, poetry, scifi, childrens, reference, and four Greek trade sizes) that seeds a book's typography, margins, folios, running heads and chapter openings. Use this whenever a task touches book design defaults, adding or editing a template/preset, font pairings, drop caps, baseline grids, or asks "where do the starter designs live". Read before editing templates/.
---

# `templates/` — DesignSpec presets

One file, `templates/__init__.py`. Each preset is a plain dict instance of `designspec/1`
(see **publisher-schemas**) covering trim size, margins, typography, grid, folio policy,
running heads, chapter openings, ornaments, fonts and colours.

## What is in the file

| Symbol | Preset |
|---|---|
| `LITERARY` | Literary Novel — 139.7 × 215.9 mm, EB Garamond, 3-line drop cap. |
| `THRILLER` | 152.4 × 228.6 mm, Source Serif/Sans, tighter margins, outer-margin running heads. |
| `MEMOIR` | 139.7 × 215.9 mm, Libertinus Serif, fleuron ornaments, warm paper (`#FAF8F5`). |
| `ACADEMIC` | 152.4 × 228.6 mm, Noto Serif/Sans + Fira Mono, no drop cap, paragraph spacing instead of indent. |
| `POETRY` | 139.7 × 215.9 mm, ragged-right, 55-char measure, generous margins, roman folios. |
| `SCIFI` | 152.4 × 228.6 mm, Merriweather. |
| `CHILDRENS` | 177.8 × 254 mm, 13 pt body, 18 pt leading, ragged right. |
| `REFERENCE` | 190.5 × 235 mm, two-column grid, 9.5 pt body, top-outside folios. |
| `_greek_preset(...)` | Factory for the four Greek sizes — written as a factory rather than four more literal dicts because the eight presets above already show what four more copies of the same forty lines cost. |
| `GREEK_17X24`, `GREEK_14X21`, `GREEK_12X17`, `GREEK_21X29` | Greek trade sizes. Leading is `GREEK_LEADING_PT = 14.173` pt = **exactly 5.00 mm**, with `baselineIncrement` matched, so every line sits on a 5 mm rule. |
| `TEMPLATES` | The registry dict: id → preset. |
| `get_template(name)` | Look up one preset. |
| `list_templates()` | id, name, trimSize, bodyFont, preferredEngine for each. |

## Rules that bite

- **`preferredEngine` is `"chrome-pagedjs"` on every preset, on purpose.** Only a CSS/Paged.js
  emitter exists today. Declaring `"typst"` would fail `design-compile`'s
  engine-implemented check (F4.1). The O1 renderer decision (`docs/BUILD_PLAN.md` §5.1 P0,
  `docs/OPTIMIZATION.md` §O1) has not been run — do not pre-empt it here.
- **Fonts must be licensable.** Every preset sources `bundled_ofl`. `design-compile` refuses
  a spec whose fonts' `allowedUses` do not cover the target output
  (`services/prepress/publisher_prepress/fontvault.py`) — this is enforced in the domain
  layer, so an unlicensed font here is a build failure, not a warning.
- **`trimSize` here must agree with the paired profile** in `profiles/`. The profile is what
  `design-compile` and `finish` actually read for geometry and bleed.
- **Keep `baselineIncrement` equal to `leading`** unless you mean to break the grid.

## Adding a preset

1. Add the dict (or extend `_greek_preset`) and register it in `TEMPLATES`.
2. Validate the shape against `schemas/designspec/designspec.schema.json`.
3. If it needs a new trim, add the matching profile under `profiles/`.
4. Run the suite with `./scripts/test.ps1` / `scripts/test.sh`.

## Related

`schemas/designspec/designspec.schema.json` · `profiles/` (the geometry that actually
binds) · `stages/design_compile_stage.py` (the CSS emitter) ·
`docs/ARCHITECTURE.md` §2.7 (one DesignSpec → three style compilers) ·
`docs/BUILD_PLAN.md` §3.8 (`services/design` — DesignSpec and its three compilers; §3.7 is the
override layer, not this).
