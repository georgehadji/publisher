"""
Tracer Bullet -- paginate stage.

Renders the resolved document into a paginated PDF via whichever engine is
available (weasyprint, playwright, etc.).

WHY THIS TAKES `doc` (doc-effective/1), NOT `html` (typescript-html/1)
ARCHITECTURE.md §1.2 stage 11: paginate's input is "doc + styles + profile", where
`doc` is `doc.effective.json` -- the AST AFTER the integrity gate (ast-assemble) and
the override layer (resolve) have both run. The previous version took `extract`'s
raw typescript-html directly, which meant a book could be paginated without its
integrity gate (or its overrides) ever having run: the DAG had an edge that
bypassed the one thing it exists to guarantee. Consuming `doc-effective/1` makes
that structurally impossible -- there is no path to a `raw-pdf/1` artifact that
does not pass through `ast-assemble` first (BUILD_PLAN.md §3.9, §5.1 P0).

Rendering reuses `extract`'s own `_ast_to_html` (the same function whose output
was proven text-complete by ast-assemble) rather than inventing a second AST-to-
HTML renderer that could drift from what the integrity gate actually verified.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
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
    """
    Check which PDF renderer is available AND actually used below.

    The previous playwright branch checked only that the subprocess could be
    spawned, never its exit code (`subprocess.run(...)` without `check=True`
    doesn't raise on a nonzero return) -- so it reported "playwright" as
    available on any machine with a Python interpreter, whether or not the
    `playwright` package was installed. Nothing below this function ever
    actually rendered via playwright either; the metrics just claimed a real
    engine ran (`renderer_type=1.0`) on the same call that printed "STUB MODE".
    Only detect an engine this function's caller can actually use.
    """
    try:
        import weasyprint
        return "weasyprint"
    except ImportError:
        pass
    return None


@stage(
    name="paginate",
    version=2,
    inputs={"doc_path": "doc-effective/1", "css_path": "text/css"},
    # NEITHER is a root input: `css_path`'s schema (text/css) is produced by
    # `design-compile`; `doc_path`'s (doc-effective/1) by `resolve`, which itself
    # requires `ast` from `ast-assemble`. Marking either as root would let a build
    # reach `paginate` without the chain that includes the text-integrity gate ever
    # having run -- the exact DAG bypass this rewire exists to close (BUILD_PLAN.md
    # F2.1, §5.1 P0). Do not "fix" a broken reachability chain by promoting an input
    # to root; fix the chain instead.
    outputs={"pdf": "raw-pdf/1", "pagemap": "pagemap/1"},
    toolchain=["render-engine"],
    fixtures="fixtures/paginate/v1",
    memory_budget_mb=256,
    queue="q.composition",
    description="Render the resolved document + CSS into a paginated PDF",
)
def paginate(ctx: StageCtx, doc_path: str | None = None, css_path: str | None = None) -> StageResult:
    """Paginate stage -- render the resolved AST + CSS into a PDF."""
    if doc_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="paginate requires 'doc_path' (from resolve)")

    doc_path_p = Path(doc_path)
    if not doc_path_p.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"Document input not found: {doc_path}")

    doc = json.loads(doc_path_p.read_bytes())
    from stages.extract_stage import _ast_to_html
    html_body = _ast_to_html(doc)

    css = ""
    if css_path and Path(css_path).exists():
        css = Path(css_path).read_text()
    else:
        from stages.design_compile_stage import _default_designspec, _emit_css
        css = _emit_css(_default_designspec())

    full_html = PAGE_TEMPLATE.format(css=css, html=html_body)

    renderer = _check_available()
    pdf_bytes = None

    if renderer == "weasyprint":
        try:
            import weasyprint
            pdf_bytes = weasyprint.HTML(string=full_html).write_pdf()
            print(f"  [paginate] Rendered PDF via weasyprint ({len(pdf_bytes)} bytes)")
        except Exception as e:
            print(f"  [paginate] weasyprint failed: {e}")

    if pdf_bytes is None:
        # No real renderer produced a PDF. A stub HTML report may stand in for one
        # ONLY when the caller has explicitly opted into stub engines (the local
        # tracer bullet). Any other caller -- in particular, anything the API can
        # trigger -- gets a hard failure rather than a "page_count" and "PDF" that
        # were never actually paginated (BUILD_PLAN.md D8: no silent fallback that
        # downgrades quality without telling the user).
        if not ctx.allow_stub_engines:
            raise StageError(
                kind=ErrorKind.INFRA,
                message="No PDF render engine available (weasyprint, playwright) "
                        "and allow_stub_engines is not set. A build cannot silently "
                        "substitute an unpaginated HTML dump for a PDF.",
            )
        pdf_bytes = full_html.encode("utf-8")
        page_count = full_html.count('<div class="chapter') + full_html.count("</div>\n") // 20 + 1
        print(f"  [paginate] STUB MODE (allow_stub_engines=True): no renderer available, "
              f"producing an HTML report instead of a real PDF")
    else:
        # weasyprint reports actual page count via its own layout, which we don't
        # currently introspect; approximate the same way as the stub path pending
        # real pagemap extraction from the renderer.
        page_count = full_html.count('<div class="chapter') + full_html.count("</div>\n") // 20 + 1

    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))

    media_type = MediaType.APPLICATION_PDF if renderer and pdf_bytes[:4] == b"%PDF" else MediaType.TEXT_HTML
    ref = cas.put(pdf_bytes, media_type=media_type)

    pagemap = {
        "schema": "pagemap/1",
        "pages": [
            {"pageNumber": i + 1, "folio": i + 1, "side": "recto" if (i + 1) % 2 == 1 else "verso",
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
                kind="pdf",
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
            "stub_engine": 0.0 if renderer else 1.0,
        },
    )
