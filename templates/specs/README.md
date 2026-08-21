# DesignSpec instances

`templates/__init__.py` holds the *presets* — the eight starter templates plus the
four Greek trade sizes — as Python dicts. This directory holds concrete
`designspec/1` **instances**: a preset with a specific book's decisions baked in,
as a file `build_local.py --designspec` (or the API) can be pointed at.

A preset is a starting point. An instance is what a particular book was built
with, and is the only thing that makes that build reproducible.

## `greek-17x24-20mm.designspec.json`

`templates.GREEK_17X24` with 20 mm margins on all four edges. Pair it with the
`Greek 17x24` vendor profile — the profile, not this file, is what
`design-compile` and `finish` read for trim and bleed; `trimSize` here agrees
with it (170×240 mm) and is kept so the spec stands on its own.

Two departures from the preset are deliberate:

- **`margins`: 20 mm on every edge**, where the preset staggers them
  (18/22 inside/outside). Equal `inside` and `outside` means the type area sits
  centred on the page with no extra allowance for the binding. At 551 pages,
  perfect binding will swallow a few millimetres of the inside margin; raise
  `inside` if the gutter reads tight on a physical proof.

- **`folio.suppressOn`: `[]`**, where the preset suppresses on
  `chapter-opening`. This is a workaround, not a preference. `_emit_css` puts
  the named page on the chapter itself (`.chapter { page: chapter-opening }`),
  so the name covers *every* page the chapter spans rather than only its first
  — and `@page chapter-opening { @bottom-center { content: none } }` therefore
  drops the folio from the whole book, not from the chapter opening. On a
  three-chapter, 551-page manuscript that leaves page numbers on the front
  matter alone. Suppressing nothing is the lesser cost until the emitter
  distinguishes a chapter's opening page from its body.

  The same over-broad name suppresses the running heads (`@top-left` /
  `@top-right`) across every chapter page, and that one *cannot* be recovered
  from the spec: `_emit_css` emits that block unconditionally.
