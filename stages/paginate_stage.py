"""
Tracer Bullet -- paginate stage.

Merges typescript HTML + compiled CSS into a PDF.
In the tracer bullet, this produces a simple HTML report or uses
whichever PDF renderer is available (weasyprint, playwright, etc.).
"""

from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
{css}
</style>
</head>
<body>
{html}
</body>
</html>"""


def _check_available() -> str | None:
    """Check which PDF renderer is available."""
    # Check for weasyprint
    try:
        import weasyprint
        return "weasyprint"
    except ImportError:
        pass
    # Check for playwright
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            capture_output=True, timeout=10,
        )
        return "playwright"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


@stage(
    name="paginate",
    version=1,
    inputs={"html_path": "typescript-html/1", "css_path": "text/css"},
    outputs={"pdf": "raw-pdf/1", "pagemap": "pagemap/1"},
    toolchain=["render-engine"],
    fixtures="fixtures/paginate/v1",
    memory_budget_mb=256,
    queue="q.composition",
    description="Merge HTML + CSS into a paginated PDF",
)
def paginate(ctx: StageCtx, html_path: str | None = None, css_path: str | None = None) -> StageResult:
    """
    Paginate stage -- merge HTML and CSS into a PDF.
    
    For the tracer bullet, we produce a merged HTML file that can be
    viewed directly, and attempt to render to PDF if a renderer is available.
    """
    if html_path is None:
        html_path = "corpus/manuscripts/minimal-novel.ast.json"
    
    # Read inputs
    if html_path.endswith(".json") and Path(html_path).exists():
        # The tracer bullet shortcut: read AST JSON directly
        ast = json.loads(Path(html_path).read_bytes())
        from stages.extract_stage import _ast_to_html
        html_body = _ast_to_html(ast)
    elif Path(html_path).exists():
        html_body = Path(html_path).read_text()
    else:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"HTML input not found: {html_path}",
        )
    
    # Get CSS
    css = ""
    if css_path and Path(css_path).exists():
        css = Path(css_path).read_text()
    else:
        # Use default CSS from design-compile
        from stages.design_compile_stage import _default_designspec, _emit_css
        css = _emit_css(_default_designspec())
    
    # Merge into page
    full_html = PAGE_TEMPLATE.format(css=css, html=html_body)
    page_count = full_html.count("<div class=\"chapter") + full_html.count("</div>\n") // 20 + 1
    
    # Try to render to PDF
    renderer = _check_available()
    pdf_bytes = None
    
    if renderer == "weasyprint":
        try:
            import weasyprint
            pdf_bytes = weasyprint.HTML(string=full_html).write_pdf()
            print(f"  [paginate] Rendered PDF via weasyprint ({len(pdf_bytes)} bytes)")
        except Exception as e:
            print(f"  [paginate] weasyprint failed: {e}, falling back to HTML-only")
    
    if pdf_bytes is None:
        # Produce a simple HTML report instead
        pdf_bytes = full_html.encode("utf-8")
        print(f"  [paginate] No PDF renderer available -- producing HTML report instead")
    
    # Store in CAS
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    
    # Determine media type
    media_type = MediaType.APPLICATION_PDF if renderer and pdf_bytes[:4] == b"%PDF" else MediaType.TEXT_HTML
    ref = cas.put(pdf_bytes, media_type=media_type)
    
    # Generate a simple pagemap
    pagemap = {
        "schema": "pagemap/1",
        "pages": [
            {"pageNumber": i+1, "folio": i+1, "side": "recto" if (i+1) % 2 == 1 else "verso",
             "widthPt": 432, "heightPt": 648, "chapterId": "ch1"}
            for i in range(page_count)
        ],
        "chapters": [
            {"chapterId": "ch1", "number": 1, "startPage": 1, "endPage": page_count, "pageCount": page_count}
        ],
    }
    pagemap_bytes = json.dumps(pagemap, indent=2).encode("utf-8")
    pm_ref = cas.put(pagemap_bytes, media_type=MediaType("application/json"))
    
    print(f"  [paginate] Produced {page_count} page(s): PDF={ref.hash}, pagemap={pm_ref.hash}")
    
    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="raw-pdf",
                hash=str(ref.hash),
                media_type=str(media_type),
                size=len(pdf_bytes),
            ),
            StageArtifactRef(
                kind="pagemap",
                hash=str(pm_ref.hash),
                media_type="application/json",
                size=len(pagemap_bytes),
            ),
        ],
        metrics={
            "page_count": page_count,
            "output_size_bytes": len(pdf_bytes),
            "renderer_type": 1.0 if renderer else 0.0,
        },
    )
