"""
Tracer Bullet -- design-compile stage.

Emits CSS from a DesignSpec. This is the "emit_css()" path from ARCHITECTURE.md §2.7.
In the tracer bullet, this produces a CSS stylesheet for Paged.js / Playwright.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_prepress.fontvault import FontLicenseViolation, validate_font_use
from profiles import load_profile


# Defaults for the page-furniture blocks, mirroring
# schemas/designspec/designspec.schema.json. They live here as well because a
# DesignSpec reaches this function as a plain dict -- nothing injects schema
# defaults at runtime -- and a `.get(key)` with no fallback would emit CSS with
# a missing value the moment a spec omits a field.
from templates import DEFAULT_LEADING_PT   # noqa: E402 -- the house 5.00mm baseline

_RUNNING_HEAD_DEFAULTS = {"sizeDelta": -3.0, "weight": "bold",
                          "case": "uppercase", "tracking": 100.0}
_FOLIO_DEFAULTS = {"sizeDelta": -1.0, "weight": "regular",
                   "case": "none", "tracking": 0.0}

CSS_WEIGHTS = {"regular": "400", "medium": "500", "semibold": "600", "bold": "700"}


def _furniture_css(block: dict, body_size: float, family: str,
                   defaults: dict) -> list[str]:
    """CSS declarations for a running head or folio, from the DesignSpec.

    The one place these turn into declarations. They used to be `font-size: 9pt`
    written out three times, which meant the DesignSpec's typography was ignored
    outright and a house rule like "running heads are body minus three" could not
    be expressed at all, let alone changed in one place.
    """
    size = body_size + float(block.get("sizeDelta", defaults["sizeDelta"]))
    weight = block.get("weight", defaults["weight"])
    case = block.get("case", defaults["case"])
    # Tracking is authored in InDesign units (1/1000 em) because InDesign is one
    # of the deliverables; CSS wants em.
    tracking = float(block.get("tracking", defaults["tracking"])) / 1000.0

    decls = [f"font-family: {family};", f"font-size: {size:g}pt;",
             f"font-weight: {CSS_WEIGHTS.get(weight, '400')};"]
    if case in ("uppercase", "lowercase"):
        decls.append(f"text-transform: {case};")
    elif case == "small-caps":
        decls.append("font-variant-caps: small-caps;")
    if tracking:
        decls.append(f"letter-spacing: {tracking:g}em;")
    return decls


def _emit_css(designspec: dict, bleed_mm: float = 0.0) -> str:
    """Emit CSS @page rules and typographic styles from a DesignSpec.

    This implements a subset of the CSS Paged Media output from ARCHITECTURE.md §2.7.

    `bleed_mm` is emitted as the CSS Paged Media `bleed` property rather than
    being added to `size` by hand. The renderer, not this function, then owns
    the box arithmetic: weasyprint keeps the page box at trim (so margins and
    the type area do not move), grows MediaBox/BleedBox outward by the bleed,
    and writes a TrimBox at the trim edge.

    Doing it by hand -- `size: trim + 2*bleed` with padded margins -- lays out
    the right geometry but leaves TrimBox == BleedBox == MediaBox in the output,
    because weasyprint writes all three from the page box. Ghostscript then
    ignores `PDFXTrimBoxToMediaBoxOffset` (those apply only to boxes the input
    lacks), fails its own TrimBox-fits-inside-BleedBox test on three identical
    non-integral rectangles, and abandons PDF/X. The bleed has to be declared
    where the renderer can see it.

    A page laid out at exactly trim -- what this emitted before -- cannot carry
    bleed at all, however much the vendor profile asks for. Preflight measured
    0.00mm against a profile demanding 3.00mm and was right to fail.
    """
    typography = designspec.get("typography", {})
    grid = designspec.get("grid", {})
    margins = designspec.get("margins", {})
    folio = designspec.get("folio", {})
    chapter_openings = designspec.get("chapterOpenings", {})
    running_heads = designspec.get("runningHeads", {})
    colors = designspec.get("colors", {})
    trim_size = designspec.get("trimSize", {})
    
    # Unit conversion
    has_unit = trim_size.get("unit", "mm") 
    w_mm = trim_size.get("width", 152)
    h_mm = trim_size.get("height", 229)
    
    body_size = typography.get("bodySize", 10.5)
    leading = typography.get("leading", DEFAULT_LEADING_PT)
    measure = typography.get("measure", 66)
    
    body_font_family = (typography.get("bodyFont") or {}).get("family", "EB Garamond")
    heading_font_family = (typography.get("headingFont") or {}).get("family", "")
    if not heading_font_family:
        heading_font_family = body_font_family
    
    top = margins.get("top", 18)
    bottom = margins.get("bottom", 20)
    inside = margins.get("inside", 15)
    outside = margins.get("outside", 20)
    gutter = margins.get("gutter", 0)
    
    text_color = colors.get("text", "#000000")
    paper_color = colors.get("paper", "#FFFFFF")
    
    lines = [
        "/* Auto-generated from DesignSpec -- emit_css() */",
        "",
        "@page {",
        f"  size: {w_mm:g}mm {h_mm:g}mm;",
        # Emitted only when there is bleed to declare, so a no-bleed profile's
        # stylesheet is byte-identical to what it was before bleed existed.
        *([f"  bleed: {bleed_mm:g}mm;"] if bleed_mm > 0 else []),
        f"  margin-top: {top}mm;",
        f"  margin-bottom: {bottom}mm;",
        f"  margin-left: {inside}mm;",
        f"  margin-right: {outside}mm;",
        "}",
        "",
        "@page :first {",
        "  @top-left { content: none; }",
        "  @top-right { content: none; }",
        "}",
        "",
        f"@page :recto {{",
        f"  margin-left: {inside}mm;",
        f"  margin-right: {outside}mm;",
        f"  @top-left {{ content: ''; }}",
        f"  @top-right {{ content: ''; }}",
        "}",
        "",
        f"@page :verso {{",
        f"  margin-left: {outside}mm;",
        f"  margin-right: {inside}mm;",
        f"  @top-left {{ content: ''; }}",
        f"  @top-right {{ content: ''; }}",
        "}",
        "",
    ]
    
    # Running heads
    rh_recto_source = running_heads.get("rectoSource", "chapter-title")
    rh_verso_source = running_heads.get("versoSource", "book-title")
    rh_style = running_heads.get("style", "centered")
    
    if rh_recto_source != "none" or rh_verso_source != "none":
        head_css = _furniture_css(running_heads, body_size, body_font_family,
                                  _RUNNING_HEAD_DEFAULTS)
        lines.extend([
            "@page :recto {",
            "  @top-left {",
            "    content: string(recto-head);",
            *(f"    {d}" for d in head_css),
            "  }",
            "}",
            "",
            "@page :verso {",
            "  @top-right {",
            "    content: string(verso-head);",
            *(f"    {d}" for d in head_css),
            "  }",
            "}",
            "",
        ])
    
    # Folio
    folio_pos = folio.get("position", "bottom-center")
    folio_style = folio.get("style", "arabic")
    folio_suppress = folio.get("suppressOn", ["chapter-opening"])
    
    if folio_pos != "none":
        folio_side_map = {
            "bottom-center": ("bottom", "center"),
            "bottom-outside": ("bottom", "outside"),
            "top-center": ("top", "center"),
            "top-outside": ("top", "outside"),
        }
        edge, align = folio_side_map.get(folio_pos, ("bottom", "center"))
        lines.extend([
            "@page {",
            f"  @{edge}-{align} {{",
            f"    content: counter(page, {folio_style});",
            *(f"    {d}" for d in _furniture_css(folio, body_size,
                                                 body_font_family, _FOLIO_DEFAULTS)),
            "  }",
            "}",
            "",
        ])
        
        if "chapter-opening" in folio_suppress:
            lines.extend([
                "@page chapter-opening {",
                f"  @{edge}-{align} {{ content: none; }}",
                "}",
                "",
            ])
    
    # Chapter opening styles
    starts_on = chapter_openings.get("startsOn", "recto")
    drop_cap = chapter_openings.get("dropCap", True)
    drop_cap_lines = chapter_openings.get("dropCapLines", 3)
    
    # Base body
    lines.extend([
        "html {",
        f"  font-family: {body_font_family};",
        f"  font-size: {body_size}pt;",
        f"  line-height: {leading}pt;",
        f"  color: {text_color};",
        "}",
        "",
        "body {",
        f"  counter-reset: chapter footnote;",
        "}",
        "",
        "p {",
        "  margin: 0;",
        "  text-indent: 1.5em;",
        "  widows: 2;",
        "  orphans: 2;",
        "}",
        "",
        "p.chapter-opening {",
        "  text-indent: 0;",
        "}",
        "",
        ".chapter {",
        f"  page: chapter-opening;",
        f"  page-break-before: {starts_on};",
        "  counter-increment: chapter;",
        "}",
        "",
        f".chapter-title {{",
        f"  font-family: {heading_font_family};",
        f"  font-size: {body_size * 1.8}pt;",
        f"  line-height: {leading * 2}pt;",
        f"  text-align: center;",
        f"  margin-top: {leading * 2}pt;",
        f"  margin-bottom: {leading}pt;",
        f"  string-set: recto-head content(text);",
        "}",
        "",
    ])
    
    if drop_cap:
        lines.extend([
            "p.chapter-opening::first-letter {",
            f"  font-size: {body_size * drop_cap_lines * 0.8}pt;",
            f"  line-height: {leading * drop_cap_lines * 0.7}pt;",
            "  float: left;",
            f"  margin-right: 0.15em;",
            "  font-weight: bold;",
            "}",
            "",
        ])
    
    # Scene break
    lines.extend([
        "hr.scene-break {",
        "  border: none;",
        "  text-align: center;",
        "  margin: 1em 0;",
        "}",
        "hr.scene-break::before {",
        "  content: '* * *';",
        "}",
        "",
    ])
    
    # Verse
    lines.extend([
        ".verse {",
        "  margin-left: 2em;",
        "  font-style: italic;",
        "}",
        ".verse-line {",
        "  text-indent: -1em;",
        "  padding-left: 1em;",
        "}",
        "",
    ])
    
    # Blockquotes
    lines.extend([
        "blockquote {",
        "  margin: 0.5em 1.5em;",
        "  font-style: italic;",
        "}",
        "blockquote.epigraph {",
        "  margin: 1em 2em;",
        "}",
        "blockquote.epigraph footer {",
        "  text-align: right;",
        "  font-size: 0.9em;",
        "}",
        "",
    ])
    
    # Code blocks
    lines.extend([
        "pre.code-block {",
        "  font-family: 'Consolas', 'Monaco', monospace;",
        "  font-size: 0.85em;",
        "  line-height: 1.4;",
        "  margin: 0.5em 0;",
        "  white-space: pre-wrap;",
        "}",
        "",
    ])
    
    # Tables
    lines.extend([
        "table {",
        "  margin: 0.5em 0;",
        "  border-collapse: collapse;",
        f"  font-size: {body_size * 0.9}pt;",
        "}",
        "th, td {",
        "  padding: 0.2em 0.5em;",
        "  border: 1px solid #ccc;",
        "  text-align: left;",
        "}",
        "th {",
        "  font-weight: bold;",
        "}",
        "caption {",
        "  font-style: italic;",
        "  margin-bottom: 0.3em;",
        "}",
        # A table split across a page break loses its heading row unless the
        # header group is declared as one; repeating it is the renderer's job,
        # this only says which rows to repeat.
        "thead { display: table-header-group; }",
        "tr { break-inside: avoid; }",
        "",
    ])

    # Figures. `break-inside: avoid` keeps a plate and its caption together: a
    # caption stranded at the top of the next page is the classic tell of a book
    # nobody looked at before printing.
    lines.extend([
        "figure {",
        "  margin: 1em 0;",
        "  text-align: center;",
        "  break-inside: avoid;",
        "}",
        "figure img {",
        # The type area is the constraint: an image wider than the text block
        # runs into the margins, and one taller than the page is dropped whole
        # by some renderers rather than scaled to fit.
        "  max-width: 100%;",
        "  max-height: 85vh;",
        "  height: auto;",
        "}",
        "figcaption {",
        f"  font-size: {body_size * 0.85}pt;",
        "  font-style: italic;",
        "  margin-top: 0.4em;",
        "}",
        "",
    ])

    # Footnotes. `float: footnote` is CSS Generated Content for Paged Media: the
    # renderer lifts the span out of the text flow into the page's footnote area
    # and numbers both the call and the note, which is why the AST carries no
    # marker text of its own.
    lines.extend([
        "@page { @footnotes { border-top: 0.5pt solid currentColor; padding-top: 0.4em; } }",
        "span.footnote {",
        "  float: footnote;",
        "  footnote-style-position: outside;",
        f"  font-size: {body_size * 0.82}pt;",
        "  text-align: left;",
        "  text-indent: 0;",
        "}",
        "::footnote-call {",
        "  content: counter(footnote, decimal);",
        "  vertical-align: super;",
        "  font-size: 0.7em;",
        "  line-height: 0;",
        "}",
        "::footnote-marker {",
        "  content: counter(footnote, decimal) '. ';",
        "  font-weight: normal;",
        "}",
        "",
    ])
    
    # Small caps
    lines.extend([
        ".small-caps {",
        "  font-variant: small-caps;",
        "}",
        "",
    ])
    
    # Front/back matter
    lines.extend([
        ".front-matter, .back-matter {",
        "  page-break-before: recto;",
        "}",
        ".titlePage {",
        "  text-align: center;",
        "  padding-top: 30%;",
        "}",
        ".copyrightPage {",
        "  font-size: 0.85em;",
        "}",
        "",
    ])
    
    # Named pages for chapter openings
    lines.extend([
        "@page chapter-opening {",
        f"  @top-left {{ content: none; }}",
        f"  @top-right {{ content: none; }}",
        "}",
        "",
    ])
    
    return "\n".join(lines)



def _fonts_in_spec(spec: dict) -> list[tuple[str, str]]:
    """(family, style) pairs referenced by a DesignSpec's typography block."""
    typ = spec.get("typography") or {}
    out = []
    for key in ("bodyFont", "displayFont", "monoFont"):
        fam = (typ.get(key) or {}).get("family")
        if fam:
            out.append((fam, (typ.get(key) or {}).get("style", "regular")))
    return out


@stage(
    name="design-compile",
    # v2: page geometry now comes from the vendor profile, and the emitted
    # @page box carries the profile's bleed. Every v1 stylesheet was laid out at
    # exactly trim, so none may be replayed for a profile that requires bleed.
    # v3: bleed is declared with the CSS `bleed` property instead of being added
    # to `size`. A v2 stylesheet renders a trim+2*bleed page whose TrimBox sits
    # on its MediaBox -- geometrically plausible, and rejected by Ghostscript.
    version=3,
    inputs={"designspec_path": "designspec/1", "profile_name": "profile/1"},
    outputs={"css": "text/css"},
    # `profile_name` is optional so that a build which omits it still renders --
    # at trim, with no bleed. That is not a silent downgrade: preflight measures
    # the bleed and fails the build against any profile that requires some.
    root_inputs=["designspec_path", "profile_name"],
    optional_root_inputs=["profile_name"],
    # Alternative impl of one step: `design-compile-typst` emits text/x-typst
    # from the same DesignSpec. stages/__init__.py selects one per process.
    implements="design-compile",
    toolchain=[],
    fixtures="fixtures/design-compile/v1",
    memory_budget_mb=64,
    queue="q.composition",
    description="Compile DesignSpec -> CSS for Paged.js rendering",
)
def design_compile(ctx: StageCtx, designspec_path: str | None = None,
                   profile_name: str | None = None) -> StageResult:
    """
    Emit CSS from a DesignSpec.
    Uses a built-in default DesignSpec for the tracer bullet.

    Only `preferredEngine: "chrome-pagedjs"` is implemented (this stage's only
    emitter is `_emit_css`). A DesignSpec naming an engine this stage cannot
    actually emit for -- most notably "typst", declared by every packaged
    template before the O1 renderer decision was run -- is `bad_input`, not a
    silently ignored field (BUILD_PLAN.md D8, F4.1). §3.8's cross-emitter
    agreement gate only stays meaningful if a declared engine is one that ran.
    """
    IMPLEMENTED_ENGINES = {"chrome-pagedjs"}

    spec = _default_designspec()

    if designspec_path:
        p = Path(designspec_path)
        if p.exists():
            spec = json.loads(p.read_bytes())

    engine = spec.get("preferredEngine")
    if engine not in IMPLEMENTED_ENGINES:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"DesignSpec declares preferredEngine={engine!r}, but only "
                    f"{sorted(IMPLEMENTED_ENGINES)} {'is' if len(IMPLEMENTED_ENGINES) == 1 else 'are'} "
                    f"implemented. Run the O1 renderer decision (BUILD_PLAN.md §5.1 "
                    f"P0) before declaring an engine with no emitter.",
        )

    # §2.10: refuse to emit a spec naming a font that is not licensed for print.
    # Enforced in the domain layer, not the UI.
    for family, style in _fonts_in_spec(spec):
        try:
            validate_font_use(family, style, "PRINT_PDF")
        except FontLicenseViolation as exc:
            raise StageError(kind=ErrorKind.POLICY_VIOLATION, message=str(exc))

    # Page geometry is the vendor profile's to decide, not the DesignSpec's.
    # They used to be declared independently and never reconciled: the built-in
    # spec said 152x229mm while "Generic 6x9" says 152.4x228.6mm, so the renderer
    # laid out one page size and preflight measured it against another. That
    # passed only because the disagreement (0.4mm) happened to sit inside
    # check_trim_size's 0.5mm tolerance. The DesignSpec keeps typography; the
    # profile owns trim and bleed.
    bleed_mm = 0.0
    if profile_name:
        profile = load_profile(profile_name)
        if profile is None:
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"Unknown vendor profile: {profile_name!r}. "
                        f"Profiles are loaded from profiles/*/*.yaml by their "
                        f"`name:` field.",
            )
        trim = profile.get("trimSize") or {}
        if trim.get("width") and trim.get("height"):
            spec = {**spec, "trimSize": trim}
        bleed_mm = float((profile.get("bleed") or {}).get("all", 0.0))

    css = _emit_css(spec, bleed_mm=bleed_mm)
    css_bytes = css.encode("utf-8")
    
    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(css_bytes, media_type=MediaType("text/css"))
    
    print(f"  [design-compile] Generated CSS -> {ref.hash} ({len(css)} bytes)")
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="css",   # must exactly equal the declared output key "css"
            hash=str(ref.hash),
            media_type="text/css",
            size=len(css_bytes),
        )],
        metrics={"css_size_bytes": len(css_bytes), "rule_count": css.count(" {")},
    )


def _default_designspec() -> dict:
    """Return a default DesignSpec for the tracer bullet."""
    return {
        "schema": "designspec/1",
        "name": "Tracer Bullet -- Literary 6x9",
        # "chrome-pagedjs", not "typst" -- see the comment on _emit_css's caller
        # below (F4.1a). Only a CSS/Paged.js emitter exists; O1 (BUILD_PLAN.md
        # §5.1 P0, §5.3) is the actual measured decision that would promote Typst
        # to the default, and it has not been run yet.
        "preferredEngine": "chrome-pagedjs",
        "trimSize": {"width": 152, "height": 229, "unit": "mm"},
        "typography": {
            "bodyFont": {"family": "EB Garamond"},
            "bodySize": 10.5,
            "leading": DEFAULT_LEADING_PT,
            "scaleRatio": 1.25,
            "measure": 66,
            "bodyAlignment": "justified",
            "paragraphIndent": 1.5,
            "opticalMargins": True,
        },
        "grid": {"type": "single", "baselineIncrement": DEFAULT_LEADING_PT},
        "margins": {"top": 18, "bottom": 20, "inside": 15, "outside": 20},
        "folio": {
            "position": "bottom-center",
            "style": "arabic",
            "suppressOn": ["chapter-opening"],
            "startNumber": 1,
        },
        "runningHeads": {
            "rectoSource": "chapter-title",
            "versoSource": "book-title",
            "style": "centered",
        },
        "chapterOpenings": {
            "startsOn": "recto",
            "dropCap": True,
            "dropCapLines": 3,
            "titleTreatment": "centered",
        },
        "fonts": [
            {"family": "EB Garamond", "source": "bundled_ofl"},
        ],
        "colors": {"text": "#000000", "paper": "#FFFFFF"},
    }
