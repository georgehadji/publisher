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


def _emit_css(designspec: dict) -> str:
    """Emit CSS @page rules and typographic styles from a DesignSpec.
    
    This implements a subset of the CSS Paged Media output from ARCHITECTURE.md §2.7.
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
    
    body_font_family = (typography.get("bodyFont") or {}).get("family", "Georgia, serif")
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
        f"  size: {w_mm}mm {h_mm}mm;",
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
        lines.extend([
            "@page :recto {",
            "  @top-left {",
            f"    content: string(recto-head);",
            f"    font-size: 9pt;",
            f"    font-family: {body_font_family};",
            "  }",
            "}",
            "",
            "@page :verso {",
            "  @top-right {",
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


@stage(
    name="design-compile",
    version=1,
    inputs={"designspec_path": "designspec/1"},
    outputs={"css": "text/css"},
    root_inputs=["designspec_path"],
    toolchain=[],
    fixtures="fixtures/design-compile/v1",
    memory_budget_mb=64,
    queue="q.composition",
    description="Compile DesignSpec -> CSS for Paged.js rendering",
)
def design_compile(ctx: StageCtx, designspec_path: str | None = None) -> StageResult:
    """
    Emit CSS from a DesignSpec.
    Uses a built-in default DesignSpec for the tracer bullet.
    """
    spec = _default_designspec()
    
    if designspec_path:
        p = Path(designspec_path)
        if p.exists():
            spec = json.loads(p.read_bytes())
    
    css = _emit_css(spec)
    css_bytes = css.encode("utf-8")
    
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(css_bytes, media_type=MediaType("text/css"))
    
    print(f"  [design-compile] Generated CSS -> {ref.hash} ({len(css)} bytes)")
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="compiled-css",
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
        "preferredEngine": "typst",
        "trimSize": {"width": 152, "height": 229, "unit": "mm"},
        "typography": {
            "bodyFont": {"family": "Georgia, serif"},
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
            {"family": "Georgia, serif", "source": "bundled_ofl"},
        ],
        "colors": {"text": "#000000", "paper": "#FFFFFF"},
    }
