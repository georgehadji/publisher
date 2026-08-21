"""
Tracer Bullet -- design-compile stage.

Emits CSS from a DesignSpec. This is the "emit_css()" path from ARCHITECTURE.md §2.7.
In the tracer bullet, this produces a CSS stylesheet for Paged.js / Playwright.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_prepress.fontvault import FontLicenseViolation, validate_font_use
from profiles import load_profile


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
    leading = typography.get("leading", 14.0)
    measure = typography.get("measure", 66)
    
    # DesignSpec's vocabulary -> CSS's. "justified" is the spec's word for what
    # CSS calls `justify`; passing it through unmapped emits an invalid value
    # that the renderer drops, which is the same ragged right by another route.
    _ALIGNMENT_TO_CSS = {
        "justified": "justify",
        "ragged-right": "left",
        "ragged-left": "right",
    }
    css_align = _ALIGNMENT_TO_CSS.get(
        typography.get("bodyAlignment", "justified"), "justify"
    )
    paragraph_indent = float(typography.get("paragraphIndent", 1.5))

    # Footnote size, stated absolutely in the spec or derived as two points
    # below the body. It used to be `body_size * 0.8`, a ratio that drifts with
    # the body size (8.4pt here, 7.6pt at a 9.5pt body) where the convention
    # this book follows is a fixed 2pt step.
    footnote_size = float(typography.get("footnoteSize", body_size - 2))

    # Footnote separator geometry. Book-design convention states it in points
    # and independently of the measure, so it is read in points here rather
    # than derived from the type area.
    # Hyphenation. Another whole DesignSpec block the emitter never emitted:
    # `hyphenation.language`, `.shortestWord` and `.zone` were readable in the
    # spec and absent from every stylesheet, so justified Greek was set with no
    # hyphenation at all -- which is what forces the loose, gappy lines that
    # full justification otherwise produces in a heavily inflected language.
    hyphenation = designspec.get("hyphenation") or {}
    hyphen_lang = hyphenation.get("language")
    shortest_word = int(hyphenation.get("shortestWord", 5))
    hyphen_zone = hyphenation.get("zone")

    _footnote_rule = designspec.get("footnoteRule") or {}
    rule_width = float(_footnote_rule.get("width", 72))
    rule_thickness = float(_footnote_rule.get("thickness", 0.25))

    body_font_family = (typography.get("bodyFont") or {}).get("family", "EB Garamond")
    heading_font_family = (typography.get("headingFont") or {}).get("family", "")
    if not heading_font_family:
        heading_font_family = body_font_family
    
    top = margins.get("top", 18)
    bottom = margins.get("bottom", 20)
    inside = margins.get("inside", 15)
    outside = margins.get("outside", 20)
    gutter = margins.get("gutter", 0)

    # The type area: trim less the two horizontal margins. Footnotes are set to
    # it explicitly because their containing area is deliberately narrower.
    measure_mm = w_mm - inside - outside
    
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
        # `:right`/`:left`, NOT `:recto`/`:verso`. CSS Paged Media defines only
        # the former, and WeasyPrint discards the whole rule on the latter
        # ("Unsupported @page selector"). Every mirrored margin and every running
        # head lived inside these two blocks, so all of it was silently dropped:
        # the book rendered with the base @page margins and no running heads at
        # all, and nothing reported a problem.
        f"@page :right {{",
        f"  margin-left: {inside}mm;",
        f"  margin-right: {outside}mm;",
        "}",
        "",
        f"@page :left {{",
        f"  margin-left: {outside}mm;",
        f"  margin-right: {inside}mm;",
        "}",
        "",
    ]
    
    # Running heads
    rh_recto_source = running_heads.get("rectoSource", "chapter-title")
    rh_verso_source = running_heads.get("versoSource", "book-title")
    rh_style = running_heads.get("style", "centered")
    verso_from_chapter = rh_verso_source == "chapter-title"
    
    if rh_recto_source != "none" or rh_verso_source != "none":
        lines.extend([
            "@page :right {",
            "  @top-right {",
            f"    content: string(recto-head);",
            f"    font-size: 9pt;",
            f"    font-family: {body_font_family};",
            "  }",
            "}",
            "",
            "@page :left {",
            "  @top-left {",
            f"    content: string(verso-head);",
            f"    font-size: 9pt;",
            f"    font-family: {body_font_family};",
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
            f"@page {{",
            f"  @{edge}-{align} {{",
            f"    content: counter(page, {folio_style});",
            f"    font-size: 9pt;",
            f"    font-family: {body_font_family};",
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
        # `bodyAlignment` and `paragraphIndent` are DesignSpec fields that this
        # emitter read into its defaults and then never emitted -- so every book
        # rendered at the initial `text-align: start`, ragged down the right-hand
        # side, however emphatically the spec said "justified". The measure is
        # the type area's full width; a paragraph that does not fill it is the
        # renderer disagreeing with the spec, not a design choice.
        "p {",
        "  margin: 0;",
        f"  text-align: {css_align};",
        f"  text-indent: {paragraph_indent:g}em;",
        "  widows: 2;",
        "  orphans: 2;",
        "}",
        "",
        "p.chapter-opening {",
        "  text-indent: 0;",
        "}",
        "",
        # `break-before`, NOT `page-break-before`. The legacy alias accepts only
        # auto|always|avoid|left|right, so `page-break-before: recto` was an
        # invalid value that WeasyPrint dropped on the floor -- chapters opened
        # wherever the text happened to reach, on odd and even pages alike, while
        # the DesignSpec said `startsOn: recto` and nothing contradicted it.
        # `break-before: recto` is CSS Fragmentation and is honoured: WeasyPrint
        # inserts a blank verso when a chapter would otherwise open on an even
        # page.
        ".chapter {",
        f"  page: chapter-opening;",
        f"  break-before: {starts_on};",
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
        # BOTH strings are set here. `verso-head` never was, so the verso
        # running head resolved to an empty string on every left-hand page while
        # the recto carried its title -- the spec's `versoSource` was simply not
        # implemented. `book-title` is still not reachable: design-compile emits
        # CSS from the DesignSpec alone and never sees the manuscript metadata,
        # so that value warns rather than silently printing nothing.
        "  string-set: recto-head content(text)"
        + (", verso-head content(text)" if verso_from_chapter else "")
        + ";",
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
        "  break-before: recto;",
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
    
    # Named pages for chapter openings.
    #
    # Emitted only when the DesignSpec actually asks for it. This block used to
    # be unconditional, and because `.chapter` carries `page: chapter-opening`
    # for every page the chapter SPANS (not merely its first), it blanked the
    # running heads across the entire book -- while `runningHeads.suppressOn`,
    # the schema field that exists to express this, was never read at all. The
    # over-broad scope is a known limitation; making it opt-in at least stops it
    # firing on specs that never requested it.
    if "chapter-opening" in (running_heads.get("suppressOn") or []):
        lines.extend([
            "@page chapter-opening {",
            f"  @top-left {{ content: none; }}",
            f"  @top-right {{ content: none; }}",
            "}",
            "",
        ])

    # Hyphenation rules. `hyphens: auto` is inert without a language on the
    # document element -- WeasyPrint picks its Pyphen dictionary from `lang`, so
    # `paginate` sets it from the AST's own metadata.language.
    #
    # NOTE ON `consecutiveHyphens`: the spec's cap on consecutive hyphenated
    # lines (a "hyphen ladder") has NO CSS property WeasyPrint implements --
    # `hyphenate-limit-lines` is rejected as an unknown property, prefixed or
    # not. It is deliberately not emitted rather than emitted-and-silently-
    # dropped, and design-compile raises a warning Diagnostic so the build says
    # out loud that this part of the spec is not being honoured.
    if hyphen_lang:
        lines.extend([
            "html {",
            "  hyphens: auto;",
            f"  hyphenate-limit-chars: {shortest_word} 3 3;",
            *([f"  hyphenate-limit-zone: {float(hyphen_zone):g}mm;"]
              if hyphen_zone else []),
            "}",
            "",
            # A hyphenated heading reads as a typographic mistake even when the
            # body wants hyphenation; same for the contents list, where a broken
            # entry collides with its leader dots.
            "h1, h2, h3, h4, h5, h6, .chapter-title, .toc p {",
            "  hyphens: none;",
            "}",
            "",
        ])

    # Every heading level takes the heading face. Only `.chapter-title` did
    # before, so `headingFont` reached the chapter openings and nothing else --
    # the numbered subsection headings that `extract` emits as <h2>/<h3>
    # inherited the body face from `html` and silently ignored the spec.
    lines.extend([
        "h1, h2, h3, h4, h5, h6 {",
        f"  font-family: {heading_font_family};",
        f"  text-align: left;",
        "  text-indent: 0;",
        f"  margin-top: {leading:.3f}pt;",
        f"  margin-bottom: {leading * 0.35:.3f}pt;",
        "  break-after: avoid;",
        "}",
        "",
        f"h2 {{ font-size: {body_size * 1.25:.2f}pt; }}",
        f"h3 {{ font-size: {body_size * 1.1:.2f}pt; }}",
        f"h4, h5, h6 {{ font-size: {body_size:.2f}pt; }}",
        "",
    ])

    # Reserved blank leaves at the front of the book (a bare `pageBreak` in the
    # front matter). Their own named page so they carry neither folio nor
    # running head -- a numbered blank is not a blank.
    lines.extend([
        ".page-break {",
        "  page: blank-leaf;",
        "  break-after: page;",
        "  height: 0;",
        "}",
        "",
        "@page blank-leaf {",
        "  @bottom-center { content: none; }",
        "  @top-left { content: none; }",
        "  @top-right { content: none; }",
        "}",
        "",
    ])

    # Footnotes (CSS GCPM). `float: footnote` takes the note out of flow and
    # lays it at the foot of the page its call lands on; WeasyPrint generates
    # the call and the marker from the same counter, so the two can never
    # disagree. Without this rule the note text renders inline, mid-page, as an
    # ordinary run of body copy.
    lines.extend([
        ".footnote {",
        "  float: footnote;",
        "  footnote-display: block;",
        f"  font-size: {footnote_size:g}pt;",
        f"  line-height: {leading * 0.78:.3f}pt;",
        "  text-indent: 0;",
        f"  text-align: {css_align};",
        # The @footnote area is only as wide as the separator rule, so each note
        # states the type area's full width itself. Without this the notes wrap
        # inside a 72pt column, two words to a line.
        f"  width: {measure_mm:g}mm;",
        "}",
        "",
        "::footnote-call {",
        "  font-size: 0.7em;",
        "  vertical-align: super;",
        "  line-height: 0;",
        "}",
        "",
        "::footnote-marker {",
        "  font-size: 0.8em;",
        "  padding-right: 0.35em;",
        "}",
        "",
        "@page {",
        "  @footnote {",
        # The rule is the AREA's top border, and the area is narrowed to the
        # rule's length; the notes get their full measure back explicitly below.
        #
        # The obvious alternative -- a full-width area with a background rule
        # sized to 72pt -- is a trap. WeasyPrint renders any `linear-gradient`
        # (and any SVG data-URI) as a TILING PATTERN, and it emitted ~17 of them
        # per page: 272,412 pattern objects across this book. Ghostscript then
        # converts every one to CMYK, and `finish` went from 48 seconds to over
        # ten minutes without completing. Measured on a 17-page fixture:
        # gradient 10.2s vs border 0.2s of gs time, 289 patterns vs zero.
        f"    border-top: {rule_thickness:g}pt solid currentColor;",
        f"    width: {rule_width:g}pt;",
        f"    padding-top: {leading * 0.35:.3f}pt;",
        f"    margin-top: {leading * 0.5:.3f}pt;",
        "  }",
        "}",
        "",
    ])

    # Table of contents. `target-counter(attr(href), page)` resolves to the page
    # the entry's chapter actually starts on, so the figures are the typeset
    # ones rather than whatever the author last typed; `leader('.')` fills the
    # gap. Entries that resolve to no chapter (subsections, which carry no id)
    # simply print without a number.
    lines.extend([
        ".toc p {",
        "  text-indent: 0;",
        "  text-align: left;",
        f"  margin-bottom: {leading * 0.15:.3f}pt;",
        "}",
        "",
        ".toc .xref {",
        "  text-decoration: none;",
        "  color: inherit;",
        "}",
        "",
        ".toc .xref::after {",
        # The spaces are load-bearing. `leader('.')` fills whatever room is left
        # on the line -- and when an entry's title happens to fill the measure
        # exactly, that room is zero, so the leader renders as nothing and the
        # page number jams against the last word ("...του Σωκράτους51"). A space
        # either side guarantees the separation the leader cannot.
        "  content: ' ' leader('.') ' ' target-counter(attr(href), page);",
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
    # v4: four emitted declarations the renderer had been silently discarding --
    # `@page :recto/:verso` (not CSS; WeasyPrint drops the whole rule, taking the
    # running heads and mirrored margins with it), `page-break-before: recto` (not a
    # legal value for the legacy alias, so chapters never opened on a recto), and a
    # missing `text-align`, which left every book ragged-right however emphatically
    # the DesignSpec said justified. Plus footnote and TOC rules. Every v3
    # stylesheet mis-renders those four things; none may be served from cache.
    version=4,
    inputs={"designspec_path": "designspec/1", "profile_name": "profile/1"},
    outputs={"css": "text/css"},
    # `profile_name` is optional so that a build which omits it still renders --
    # at trim, with no bleed. That is not a silent downgrade: preflight measures
    # the bleed and fails the build against any profile that requires some.
    root_inputs=["designspec_path", "profile_name"],
    optional_root_inputs=["profile_name"],
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

    # §3.15: a finding the user needs to know about travels as a Diagnostic, not
    # as a silence. A spec field the target engine cannot honour is exactly that
    # -- the alternative is emitting a declaration the renderer discards, which
    # is how `bodyAlignment`, the hyphenation block and `@page :recto` all came
    # to be quietly ignored for so long.
    warnings = []
    hyph = spec.get("hyphenation") or {}
    if hyph.get("consecutiveHyphens") is not None:
        warnings.append(Diagnostic(
            code="hyphen_ladder_not_enforced",
            severity="warning",
            human_message=(
                f"DesignSpec asks for at most {hyph['consecutiveHyphens']} "
                "consecutive hyphenated lines, but the chrome-pagedjs/WeasyPrint "
                "emitter has no property for it -- `hyphenate-limit-lines` is not "
                "implemented. Hyphenation is applied; the ladder limit is not."
            ),
            suggested_fix=(
                "Accept the ladders, or widen the measure / loosen "
                "hyphenate-limit-zone so they arise less often."
            ),
            source_ref="designspec:hyphenation.consecutiveHyphens",
        ))
        print(f"    WARN hyphen_ladder_not_enforced: consecutiveHyphens="
              f"{hyph['consecutiveHyphens']} cannot be enforced by this engine")

    verso_source = (spec.get("runningHeads") or {}).get("versoSource")
    if verso_source == "book-title":
        warnings.append(Diagnostic(
            code="verso_head_not_available",
            severity="warning",
            human_message=(
                "DesignSpec asks for the book title in the verso running head, "
                "but design-compile emits CSS from the DesignSpec alone and never "
                "sees the manuscript's metadata, so there is no string to set. "
                "Verso running heads will be blank."
            ),
            suggested_fix='Use runningHeads.versoSource: "chapter-title".',
            source_ref="designspec:runningHeads.versoSource",
        ))
        print("    WARN verso_head_not_available: versoSource='book-title' has no "
              "source in the emitter; verso heads will be blank")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="css",   # must exactly equal the declared output key "css"
            hash=str(ref.hash),
            media_type="text/css",
            size=len(css_bytes),
        )],
        metrics={"css_size_bytes": len(css_bytes), "rule_count": css.count(" {")},
        warnings=warnings,
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
            "leading": 14.0,
            "scaleRatio": 1.25,
            "measure": 66,
            "bodyAlignment": "justified",
            "paragraphIndent": 1.5,
            "opticalMargins": True,
        },
        "grid": {"type": "single", "baselineIncrement": 14.0},
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
