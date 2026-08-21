---
name: publisher-profiles
description: Map of the `profiles/` folder — vendor output profiles as versioned YAML data (KDP, IngramSpark, Lulu, generic 6x9, Greek trade sizes) plus the loader. Use this whenever a task touches trim size, bleed, PDF/X spec, colour space, vendor requirements, adding or changing a print vendor, or asks "what does preflight measure against". Read before editing anything under profiles/.
---

# `profiles/` — vendor specs as data, not code

Each YAML file is an instance of `profile/1` (see **publisher-schemas**). `design-compile`,
`finish`, `preflight`, `cover` and `cover-preflight` all take `profile_name` as a **root
input** — vendor specs are loaded from this folder, never produced by a stage.

## Files

| File | What it holds |
|---|---|
| `__init__.py` | `load_profile(name)`. Scans every `profiles/` subdirectory for YAML containing the named profile; files may be multi-document (`---` separated) to hold several profiles. Falls back to a CWD-relative `profiles/` when the package path does not resolve. |
| `generic/6x9.yaml` | `Generic 6x9` — safe defaults for testing. 152.4 × 228.6 mm, 3.0 mm bleed. The tracer bullet's default. |
| `kdp/us-trade.yaml` | `KDP US Trade 6x9`, v2025.1. 152.4 × 228.6 mm, 3.175 mm bleed. Source: KDP help topic G201834340. |
| `ingramspark/us-trade.yaml` | `IngramSpark US Trade 6x9`, v2025.1. Same trim, 3.175 mm bleed. |
| `lulu/us-trade.yaml` | `Lulu US Trade 6x9`, v2025.1. Same trim, 3.175 mm bleed. |
| `greek/standard-sizes.yaml` | Four Greek trade sizes, multi-document. **Exact whole-centimetre trims** — 17×24 cm is 170×240 mm, not a rounded inch conversion. All four declare 3 mm bleed on every side. |

Pair each Greek profile with the same-named preset in `templates/__init__.py`
(`GREEK_17X24`, `GREEK_14X21`, `GREEK_12X17`, `GREEK_21X29`).

## Rules that bite

- **The profile is what actually drives geometry.** A DesignSpec preset's `trimSize` is
  carried so the preset stands alone in a listing; `design-compile` and `finish` read the
  *profile*. If the two disagree, the profile wins and the preset is misleading — keep them
  in sync.
- **Bleed only exists if all three stages agree.** `design-compile` grows the CSS page box,
  the renderer widens MediaBox/BleedBox, `finish` insets the TrimBox back to trim.
  `stages/tests/test_bleed_geometry.py` is the test that holds those three to one number.
- **`vendorProfileVersion` is part of the record.** Bump it when a vendor changes their
  spec; builds are reproducible against a pinned vendor version, not "whatever the site
  says today".

## Adding a vendor

1. Add `profiles/<vendor>/<size>.yaml` with `schema: profile/1`.
2. Validate: `python cli.py schema validate profiles/<vendor>/<size>.yaml`.
3. If it needs a matching page design, add a preset in `templates/__init__.py`.
4. Run `./scripts/test.ps1 -k profiles` (or `scripts/test.sh -k profiles`) —
   `services/prepress/tests/test_profiles.py` covers loading.

## Related

`schemas/profile/profile.schema.json` (the contract) · `services/prepress/publisher_prepress/preflight.py`
(what enforces it) · `services/prepress/publisher_prepress/geometry.py` (spine/bleed arithmetic) ·
`templates/` (the design side) · `stages/prepress_stages.py`, `stages/finish_stage.py`.
