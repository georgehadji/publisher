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
import re
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
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


def _escape_attr(value: str) -> str:
    """Minimal attribute escaping for the one interpolated attribute here."""
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


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


# Characters of body text that fit on one typeset page. Only ever used to
# estimate extent when no renderer paginated the document; a real render
# reports its own page count and this constant is not consulted.
CHARS_PER_PAGE = 1800

_CHAPTER_DIV = re.compile(
    r'<div class="chapter" id="(?P<id>[^"]*)" data-number="(?P<number>[^"]*)">'
)
_TAG = re.compile(r"<[^>]+>")


def _chapters_in_order(html_body: str) -> list[dict]:
    """Chapter id/number/text-length triples, in document order.

    Read back out of the rendered HTML rather than the AST because the HTML is
    what the renderer actually paginates -- if `extract` ever stops emitting a
    chapter, the pagemap should lose it too instead of describing a chapter
    that is not in the PDF.
    """
    matches = list(_CHAPTER_DIV.finditer(html_body))
    chapters: list[dict] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html_body)
        text = _TAG.sub(" ", html_body[m.start():end])
        try:
            number = int(m.group("number"))
        except ValueError:
            number = i + 1
        chapters.append(
            {
                "chapterId": m.group("id") or f"ch{i + 1}",
                "number": number,
                "textLength": len(" ".join(text.split())),
            }
        )
    return chapters


def _estimate_page_count(html_body: str) -> int:
    """Approximate extent from text volume, for stub mode only."""
    text = " ".join(_TAG.sub(" ", html_body).split())
    return max(1, -(-len(text) // CHARS_PER_PAGE))


def _chapter_start_pages(chapters: list[dict], rendered_pages) -> dict[str, int]:
    """First page of each chapter, from the renderer's own anchor positions.

    `extract` emits every chapter as `<div class="chapter" id="...">`, so each
    chapter id becomes a named anchor that weasyprint records on whichever page
    it laid the element out.
    """
    wanted = {c["chapterId"] for c in chapters}
    starts: dict[str, int] = {}
    for page_number, page in enumerate(rendered_pages, start=1):
        for anchor in getattr(page, "anchors", {}) or {}:
            if anchor in wanted and anchor not in starts:
                starts[anchor] = page_number
    return starts


def _build_pagemap(chapters: list[dict], page_count: int, rendered_pages) -> dict:
    """Assemble a `pagemap/1` describing which chapter occupies which page.

    With a real render this is measured. Without one it is apportioned by text
    volume -- still an estimate, but one that at least tracks the manuscript
    instead of claiming, as this stage used to, that a single chapter `ch1`
    spans every page of every book.
    """
    if not chapters:
        chapters = [{"chapterId": "ch1", "number": 1, "textLength": 1}]

    if rendered_pages is not None:
        starts_by_id = _chapter_start_pages(chapters, rendered_pages)
    else:
        total = sum(c["textLength"] for c in chapters) or 1
        starts_by_id = {}
        cursor = 0
        for c in chapters:
            starts_by_id[c["chapterId"]] = min(page_count, 1 + cursor * page_count // total)
            cursor += c["textLength"]

    # A chapter the renderer never placed (empty, or dropped in layout) inherits
    # its predecessor's page rather than defaulting to page 1 out of order.
    starts: list[int] = []
    previous = 1
    for c in chapters:
        previous = max(previous, starts_by_id.get(c["chapterId"], previous))
        starts.append(previous)

    entries = []
    for i, c in enumerate(chapters):
        start = starts[i]
        end = (starts[i + 1] - 1) if i + 1 < len(chapters) else page_count
        end = max(start, min(end, page_count))
        entries.append(
            {
                "chapterId": c["chapterId"],
                "number": c["number"],
                "startPage": start,
                "endPage": end,
                "pageCount": end - start + 1,
            }
        )

    owner_of_page = {}
    for e in entries:
        for p in range(e["startPage"], e["endPage"] + 1):
            owner_of_page.setdefault(p, e["chapterId"])
    first_chapter = entries[0]["chapterId"]

    width_pt, height_pt = 432.0, 648.0
    if rendered_pages:
        width_pt = float(getattr(rendered_pages[0], "width", width_pt))
        height_pt = float(getattr(rendered_pages[0], "height", height_pt))

    pages = [
        {
            "pageNumber": p,
            "folio": p,
            "side": "recto" if p % 2 == 1 else "verso",
            "widthPt": width_pt,
            "heightPt": height_pt,
            # Pages before the first chapter are front matter; the schema still
            # requires a chapterId, so they are attributed to chapter one.
            "chapterId": owner_of_page.get(p, first_chapter),
        }
        for p in range(1, page_count + 1)
    ]

    return {"schema": "pagemap/1", "pages": pages, "chapters": entries}


@stage(
    name="paginate",
    # v5: the rendered document element now carries the manuscript's own
    # language instead of a hardcoded lang="en". WeasyPrint picks its Pyphen
    # dictionary from that attribute, so every v4 render of a non-English book
    # was hyphenated with ENGLISH patterns -- wrong break points, silently.
    version=5,   # v4: pagemap/1 declared terminal (U6)
    inputs={"doc_path": "doc-effective/1", "css_path": "text/css"},
    # NEITHER is a root input: `css_path`'s schema (text/css) is produced by
    # `design-compile`; `doc_path`'s (doc-effective/1) by `resolve`, which itself
    # requires `ast` from `ast-assemble`. Marking either as root would let a build
    # reach `paginate` without the chain that includes the text-integrity gate ever
    # having run -- the exact DAG bypass this rewire exists to close (BUILD_PLAN.md
    # F2.1, §5.1 P0). Do not "fix" a broken reachability chain by promoting an input
    # to root; fix the chain instead.
    outputs={"pdf": "raw-pdf/1", "pagemap": "pagemap/1"},
    terminal_outputs=["pagemap"],   # delivered via the API, never consumed
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

    # `hyphens: auto` is inert without this. WeasyPrint chooses its Pyphen
    # dictionary from the document element's `lang`, and the template hardcoded
    # "en" -- so a Greek manuscript asking for hyphenation would have been
    # hyphenated with ENGLISH patterns, breaking Greek words at points no Greek
    # dictionary would allow. The AST already carries the real language.
    lang = (doc.get("metadata") or {}).get("language") or "en"
    full_html = PAGE_TEMPLATE.format(css=css, html=html_body, lang=_escape_attr(lang))

    chapters = _chapters_in_order(html_body)

    renderer = _check_available()
    pdf_bytes = None
    rendered_pages = None

    if renderer == "weasyprint":
        try:
            import weasyprint
            # `.render()` before `.write_pdf()` so the laid-out document itself is
            # available. It is the only authority on how many pages there are and
            # where each chapter landed; counting tags in the *input* HTML cannot
            # know either, because pagination is what the renderer decides.
            document = weasyprint.HTML(string=full_html).render()
            pdf_bytes = document.write_pdf()
            rendered_pages = document.pages
            print(f"  [paginate] Rendered PDF via weasyprint "
                  f"({len(pdf_bytes)} bytes, {len(rendered_pages)} pages)")
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
        page_count = _estimate_page_count(html_body)
        print(f"  [paginate] STUB MODE (allow_stub_engines=True): no renderer available, "
              f"producing an HTML report instead of a real PDF")
        print(f"  [paginate] page_count is a text-volume ESTIMATE ({page_count}), not a "
              f"layout result -- no renderer paginated this document")

    if rendered_pages is not None:
        page_count = len(rendered_pages)

    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))

    media_type = MediaType.APPLICATION_PDF if renderer and pdf_bytes[:4] == b"%PDF" else MediaType.TEXT_HTML
    ref = cas.put(pdf_bytes, media_type=media_type)

    pagemap = _build_pagemap(chapters, page_count, rendered_pages)
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
            # Whether `page_count` was measured from a laid-out document or
            # merely estimated from text volume. Anything that prices a spine
            # or a print run off page_count must refuse the estimated variety.
            "page_count_measured": 1.0 if rendered_pages is not None else 0.0,
            "chapter_count": float(len(chapters)),
            "output_size_bytes": len(pdf_bytes),
            "renderer_type": 1.0 if renderer else 0.0,
            "stub_engine": 0.0 if renderer else 1.0,
        },
    )
