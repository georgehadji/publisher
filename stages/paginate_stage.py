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

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef
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


def _text_outside_page(rendered_pages) -> dict[int, int]:
    """Pages carrying text drawn outside the page box, and how many characters.

    WeasyPrint 62.3 does not split a footnote across pages. A note taller than the
    area it is given is laid out in full anyway and the excess is drawn BELOW the
    page edge -- present in the PDF content stream, absent from the printed sheet.

    Nothing else in the pipeline can see this. `ast-assemble` compares text before
    layout, and every PDF text extractor (pdfplumber, pdftotext, Ghostscript)
    returns off-page glyphs like any other, so a text-equality check on the
    rendered PDF passes with the text missing from the page. Only the laid-out
    geometry shows it, which is why it is measured here.
    """
    from weasyprint.formatting_structure import boxes as _boxes

    lost: dict[int, int] = {}
    for index, page in enumerate(rendered_pages, start=1):
        height = page.height
        count = 0

        def walk(box):
            nonlocal count
            if isinstance(box, _boxes.TextBox):
                # position_y is the top of the line box in page coordinates.
                if box.position_y < 0 or box.position_y + box.height > height:
                    count += len(box.text or "")
                return
            for child in getattr(box, "children", ()) or ():
                walk(child)

        walk(page._page_box)
        if count:
            lost[index] = count
    return lost


SPLIT_PASSES = 6          # loop cap; each pass moves text strictly later


def _boxes_named(box, name, out=None):
    out = [] if out is None else out
    if type(box).__name__ == name:
        out.append(box)
    for child in getattr(box, "children", ()) or ():
        _boxes_named(child, name, out)
    return out


def _overflowing_notes(page) -> list[tuple[str, int, int]]:
    """(note id, lines laid out, lines falling outside the type area) per note.

    Measured purely as geometry. An earlier version of this reconstructed the
    lost text by re-joining the laid-out lines and matching it against the
    source, and that cannot be made reliable: `overflow-wrap: break-word`
    breaks a long URL with no hyphen to mark the break, so rejoining inserts a
    space the source has not got, and this manuscript also uses "-" as a dash
    welded to a word, so undoing hyphenation eats a real character and fuses
    two words ("για" + "Το" -> "γιαΤο"). The box tree already knows which note
    each line belongs to, which needs no text at all.

    The test is the TYPE AREA, not the paper edge, because a note can overrun
    in either direction. Uncapped, the `@footnote` area grows UPWARD from the
    bottom of the page and the note's opening lines end up above the top margin
    (measured on a fixture: a 2500-word note laid out from y=-1737). Capped
    with `max-height` -- which this pipeline always emits -- the area is pinned
    and the note's tail runs off the bottom instead. A rule keyed to the paper
    edge would miss the first case entirely, and would also ignore a note that
    merely spills into the bottom margin, where it prints but is liable to be
    trimmed off.
    """
    page_box = page._page_box
    top = page_box.content_box_y()
    bottom = top + page_box.height

    found: list[tuple[str, int, int]] = []
    for area in _boxes_named(page_box, "FootnoteAreaBox"):
        for block in area.children:
            element = getattr(block, "element", None)
            note_id = element.get("id") if element is not None else None
            if not note_id:
                continue
            lines = _boxes_named(block, "LineBox")
            if not lines:
                continue
            outside = sum(1 for line in lines
                          if line.position_y < top - 0.5
                          or line.position_y + line.height > bottom + 0.5)
            if outside:
                found.append((note_id, len(lines), outside))
    return found


_ANCHOR = re.compile(r"^ax\d+$")

# One empty anchor roughly every this many words of body text. A page of this
# book holds about 480 words, so this puts several on every page.
ANCHOR_EVERY_WORDS = 80


def _note_text_pages(pages) -> dict[str, int]:
    """Note id -> the page its TEXT is set on.

    Not the same as the note's page anchor. A footnote span lives in the body
    where its call is, so `page.anchors` reports the page of the CALL -- which
    is often the page before the one the note is actually set on, once the area
    is busy. Adjacency has to be judged on where the reader sees the text.
    """
    where: dict[str, int] = {}
    for number, page in enumerate(pages, start=1):
        for area in _boxes_named(page._page_box, "FootnoteAreaBox"):
            for block in area.children:
                element = getattr(block, "element", None)
                note_id = element.get("id") if element is not None else None
                if note_id:
                    where.setdefault(note_id, number)
    return where


def _tag_layout_anchors(html_body: str) -> tuple[str, str]:
    """Anchor the document so the renderer can be asked where things landed.

    Three things get an id, all sharing ONE counter so that comparing ids
    compares document order:

    * every paragraph, on its `<p>`;
    * every footnote, on its `<span>`, as `fn...`;
    * an empty `<span>` every ~80 words of body text.

    The empty spans are what make a continuation land on the RIGHT page.
    `page.anchors` can only report elements the renderer actually placed, so
    without them the only attachment points are paragraph starts -- and a page
    that a long paragraph merely flows through has none. Measured on this
    manuscript, that is exactly what happened to note 373: page 512 begins no
    paragraph, so its third chunk skipped to 513 and the reader turning the
    page found nothing. An empty inline span occupies no space and draws
    nothing, so scattering them changes no layout while giving every page an
    attachment point.

    Ids carry no text, so neither hard gate sees them, and `ast-assemble` has
    already run by the time paginate builds this HTML.
    """
    counter = iter(range(10 ** 9))
    notes = iter(range(10 ** 9))

    html_body = re.sub(r'<span class="footnote">',
                       lambda m: f'<span class="footnote" id="fn{next(notes)}">',
                       html_body)
    html_body = re.sub(r"<p(?![^>]*\sid=)([ >])",
                       lambda m: f'<p id="ax{next(counter)}"' + m.group(1),
                       html_body)

    # Word anchors. Inserted only in body text at the top level of a paragraph:
    # never inside a tag, and never inside a footnote, whose words are not body
    # copy and whose own splitting must not be disturbed.
    out: list[str] = []
    pos = 0
    words = 0
    depth_footnote = 0
    for token in re.finditer(r"<[^>]+>|[^<]+", html_body):
        text = token.group(0)
        if text.startswith("<"):
            if re.match(r'<span class="footnote(-continued)?"', text):
                depth_footnote += 1
            elif text == "</span>" and depth_footnote:
                depth_footnote -= 1
            out.append(text)
            continue
        if depth_footnote:
            out.append(text)
            continue
        # split on whitespace runs, keeping them, so nothing is reflowed
        pieces = re.split(r"(\s+)", text)
        rebuilt = []
        for piece in pieces:
            rebuilt.append(piece)
            if piece and not piece.isspace():
                words += 1
                if words % ANCHOR_EVERY_WORDS == 0:
                    rebuilt.append(f'<span id="ax{next(counter)}"></span>')
        out.append("".join(rebuilt))
    return "".join(out), ""


def _anchor_index(anchor: str) -> int:
    return int(anchor[2:])


def _insert_after_anchor(html_body: str, anchor: str, fragment: str) -> str | None:
    """Put `fragment` immediately after the element carrying this id."""
    opening = re.search(rf'<(p|span) id="{re.escape(anchor)}"[^>]*>', html_body)
    if opening is None:
        return None
    if opening.group(1) == "span":
        # an empty word anchor: skip its closing tag too
        tail = html_body[opening.end():]
        if not tail.startswith("</span>"):
            return None
        at = opening.end() + len("</span>")
    else:
        at = opening.end()
    return html_body[:at] + fragment + html_body[at:]


def _note_body(html_body: str, note_id: str) -> tuple[str, str, int] | None:
    """The class, inner text, and home paragraph index of the note with this id.

    The home paragraph is the one the note's call sits in. It is what keeps
    successive continuations of the same note in reading order: a tail may only
    be re-anchored to a paragraph that comes strictly after it.
    """
    match = re.search(
        rf'<span class="(footnote|footnote-continued)" id="{re.escape(note_id)}">(.*?)</span>',
        html_body, re.S)
    if match is None:
        return None
    home = -1
    for prior in re.finditer(r'id="ax(\d+)"', html_body[:match.start()]):
        home = int(prior.group(1))
    return match.group(1), match.group(2), home


def _split_overlong_notes(html_body: str, pages) -> tuple[str, int, list[str]]:
    """Cut every note that overruns its page and re-anchor each tail later.

    Returns the rewritten HTML, the (note, continuation) pairs it created, and
    the notes that could not be cut -- reported rather than left silently
    overflowing.

    How much to move is taken from the line counts: a note laid out in `total`
    lines of which `outside` do not fit keeps the same proportion of its words.
    That is an estimate, because the first line is short by the width of the
    marker and the last kept line may be short too -- so the caller re-renders
    and runs this again. Every pass moves text strictly later and the split
    itself is lossless by construction (the two halves are a partition of the
    same word list), so an imprecise estimate costs a pass, never a word.

    Edits are made by id rather than by offset, so cutting several notes in one
    pass cannot corrupt the positions of the ones cut after it.
    """
    skipped: list[str] = []
    carried: list[tuple[str, str]] = []
    continuation_seq = 0

    for page_number, page in enumerate(pages, start=1):
        for note_id, total, outside in _overflowing_notes(page):
            found = _note_body(html_body, note_id)
            if found is None:
                skipped.append(f"page {page_number}: note {note_id} is laid out but "
                               f"no longer present in the document")
                continue
            note_class, body, home = found
            if "<" in body:
                skipped.append(f"page {page_number}: note {note_id} carries inline "
                               f"markup, and splitting it would break the tags")
                continue

            words = body.split()
            if len(words) < 2:
                skipped.append(f"page {page_number}: note {note_id} is a single "
                               f"word and cannot be divided")
                continue

            keep_ratio = max(0.0, (total - outside) / total)
            keep_count = int(len(words) * keep_ratio)
            # Round down a little: carrying slightly too much costs nothing,
            # while carrying too little costs another full re-render.
            keep_count = max(1, min(len(words) - 1, keep_count - 2))
            keep = " ".join(words[:keep_count])
            carry = " ".join(words[keep_count:])

            # The tail must hang off an element the renderer puts on a LATER
            # page, AND one that comes after this note in document order.
            #
            # Both halves matter. A page's anchor list includes paragraphs that
            # merely continue onto it from the page before, so the earliest
            # anchor on the next page can be the very paragraph this note
            # already lives in -- and inserting there puts the tail BEFORE the
            # note it continues. Measured on this manuscript: note 373's second
            # chunk was re-anchored into its own paragraph, and the note then
            # read 510 -> 512 -> 511. Requiring `index > home` removes that
            # whole class of inversion and keeps successive chunks monotonic,
            # because each new target becomes the next chunk's home.
            target = None
            for later in pages[page_number:]:
                ids = sorted(_anchor_index(a)
                             for a in (getattr(later, "anchors", {}) or {})
                             if _ANCHOR.match(a))
                ids = [i for i in ids if i > home]
                if ids:
                    target = f"ax{ids[0]}"
                    break
            if target is None:
                skipped.append(f"page {page_number}: no paragraph begins on any "
                               f"later page, so note {note_id} has nowhere to "
                               f"carry to")
                continue

            continuation_seq += 1
            cont_id = f"{note_id}c{continuation_seq}"
            html_body, hits = re.subn(
                rf'(<span class="{note_class}" id="{re.escape(note_id)}">).*?(</span>)',
                lambda m: m.group(1) + keep + m.group(2),
                html_body, count=1, flags=re.S)
            if not hits:
                skipped.append(f"page {page_number}: could not rewrite note {note_id}")
                continue
            continuation = (f'<span class="footnote-continued" id="{cont_id}">'
                            f'{carry}</span>')
            placed = _insert_after_anchor(html_body, target, continuation)
            if placed is None:
                skipped.append(f"page {page_number}: anchor {target} vanished "
                               f"between render and rewrite")
                continue
            html_body = placed
            carried.append((note_id, cont_id))

    return html_body, carried, skipped


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
    version=12,  # v6: detect text beyond the page edge; v7: split over-long
                 # footnotes; v8: keep a split note in reading order. v7 could
                 # re-anchor a tail into the paragraph the note already lived
                 # in, putting the continuation BEFORE the text it continues --
                 # measured on this manuscript, note 373 read 510 -> 512 -> 511.
                 # Every v7 PDF of a book with a split note is wrong that way.
                 # v9: word anchors, so a continuation lands on the very next
                 # page even when no paragraph begins there. Under v8 note 373
                 # skipped page 512 entirely. v10: report a continuation that
                 # does not resume on the very next page; v11 judges that from
                 # where the note TEXT is set, not from its call.
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
    # Paragraph ids are added here rather than in `extract` because they exist
    # only to let this stage ask the renderer which page a paragraph landed on.
    # They are layout scaffolding, not content, and nothing downstream reads them.
    html_body, _ = _tag_layout_anchors(_ast_to_html(doc))

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

    notes_split = 0
    split_pairs: list[tuple[str, str]] = []
    split_skipped: list[str] = []

    if renderer == "weasyprint":
        try:
            import weasyprint
            # `.render()` before `.write_pdf()` so the laid-out document itself is
            # available. It is the only authority on how many pages there are and
            # where each chapter landed; counting tags in the *input* HTML cannot
            # know either, because pagination is what the renderer decides.
            document = weasyprint.HTML(string=full_html).render()

            # Carry the tail of any note too tall for its page onto the next one.
            #
            # This has to be a loop, and it has to re-render: a note can only be
            # cut once the renderer has said where the page ended, and moving a
            # tail onto the following page changes THAT page's layout, which can
            # push its own notes over in turn. Each pass moves text strictly
            # later in the document, so the loop terminates; SPLIT_PASSES is a
            # backstop, not the expected exit.
            for _ in range(SPLIT_PASSES):
                html_body, cut, skipped = _split_overlong_notes(
                    html_body, document.pages)
                split_skipped = skipped
                if not cut:
                    break
                split_pairs.extend(cut)
                notes_split += len(cut)
                full_html = PAGE_TEMPLATE.format(
                    css=css, html=html_body, lang=_escape_attr(lang))
                document = weasyprint.HTML(string=full_html).render()
                print(f"  [paginate] Split an over-long footnote "
                      f"({notes_split} so far); re-rendered at "
                      f"{len(document.pages)} pages")

            pdf_bytes = document.write_pdf()
            rendered_pages = document.pages
            print(f"  [paginate] Rendered PDF via weasyprint "
                  f"({len(pdf_bytes)} bytes, {len(rendered_pages)} pages)")
        except StageError:
            raise
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

    warnings: list[Diagnostic] = []
    overflow = _text_outside_page(rendered_pages) if rendered_pages is not None else {}
    if overflow:
        pages = ", ".join(str(p) for p in sorted(overflow)[:10])
        total = sum(overflow.values())
        warnings.append(Diagnostic(
            code="text_outside_page",
            severity="warning",
            human_message=(
                f"{total} characters on {len(overflow)} page(s) are laid out beyond "
                f"the page edge and will not print: page(s) {pages}. An over-long "
                "footnote is normally cut and carried to the next page, so this "
                "means the carry could not be completed."
                + (" Reasons: " + "; ".join(split_skipped) if split_skipped else "")
            ),
            suggested_fix=(
                "Check the reasons above. A note carrying inline markup is not "
                "split automatically; a note on the last page has nowhere to "
                "carry to. Shortening the note, or moving the quotation into the "
                "body as a block quote, resolves either case."
            ),
            source_ref="paginate:footnote-overflow",
        ))
        print(f"    WARN text_outside_page: {total} chars on {len(overflow)} page(s) "
              f"({pages}) fall outside the page box and will not print")
        for reason in split_skipped:
            print(f"      - {reason}")

    if notes_split:
        print(f"  [paginate] {notes_split} over-long footnote(s) carried onto a "
              f"following page")

    # A continuation belongs on the page immediately after the one it continues.
    # It usually lands there, but not always, and the reason is outside this
    # stage's control: attaching a tail to a page also invites WeasyPrint to
    # flow its OWN deferred notes onto it, the body shrinks, the anchor the tail
    # was pinned to slides to the next page, and the note follows. The text is
    # complete and in reading order either way, so this is reported rather than
    # fought -- a reader who turns the page and finds nothing needs to be told,
    # and the fix is editorial (a shorter note) not typographic.
    if rendered_pages is not None and split_pairs:
        page_of = _note_text_pages(rendered_pages)
        gaps = [(parent, child, page_of.get(parent), page_of.get(child))
                for parent, child in split_pairs
                if page_of.get(parent) is not None
                and page_of.get(child) is not None
                and page_of[child] != page_of[parent] + 1]
        if gaps:
            described = "; ".join(
                f"the part on page {a} resumes on page {c}, not {a + 1}"
                for _, _, a, c in gaps)
            warnings.append(Diagnostic(
                code="footnote_continuation_not_adjacent",
                severity="warning",
                human_message=(
                    f"{len(gaps)} split footnote(s) resume later than the page "
                    f"immediately after the one they start on: {described}. The "
                    "text is complete and in order, but a reader turning the "
                    "page finds the note missing and picks it up further on."
                ),
                suggested_fix=(
                    "Shorten the note, or move the quotation into the body as a "
                    "block quote, so that it does not need to be carried at all."
                ),
                source_ref="paginate:footnote-continuation",
            ))
            print(f"    WARN footnote_continuation_not_adjacent: {described}")

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
            # Characters laid out beyond the page edge. Non-zero means the PDF
            # carries text that will not appear on the printed sheet.
            "text_outside_page_chars": float(sum(overflow.values())),
            # How many over-long notes had to be cut and carried onto the next
            # page. Zero is the common case; a sudden rise means the design got
            # tighter or the manuscript grew notes it cannot hold.
            "footnotes_split": float(notes_split),
        },
        warnings=warnings,
    )
