"""
Shared rendering primitives -- E1.3 (docs/ARCHITECTURE_SCORE_10_PLAN.md).

`ast_to_html` and `emit_css` used to live as private, cross-sibling imports:
`paginate_stage.py` reached into `stages.extract_stage._ast_to_html` and
`stages.design_compile_stage._emit_css`, and `idml_stage.py`/`typst_stages.py`
each reached into `_ast_to_html` too. This is their one public home instead
(E1: one home per concern).

CONSTRAINT THAT MUST SURVIVE ANY FUTURE CHANGE HERE: `extract` and `paginate`
must call the SAME function object. Two copies of either function would
silently break the guarantee that pagination renders exactly what the
text-integrity gate verified against `extract`'s output -- see
`stages/tests/test_shared_rendering.py`, which asserts identity, not just
equality of output.
"""

from __future__ import annotations

import html as _html

# The house 5.00mm baseline -- see design_compile_stage.py's original comment:
# a DesignSpec reaches emit_css() as a plain dict with no schema defaults
# applied at runtime, so a `.get(key)` with no fallback would emit CSS with a
# missing value the moment a spec omits a field.
from templates import DEFAULT_LEADING_PT

# ── ast_to_html (moved from extract_stage.py) ───────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
</head>
<body>
{body}
</body>
</html>"""

# Extensions for the image types Word actually embeds. The renderers get files
# on disk, and weasyprint, Typst and InDesign all decide how to decode by
# extension -- an extensionless blob is refused by all three.
MEDIA_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/tiff": "tif",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
    "image/x-emf": "emf",
    "image/x-wmf": "wmf",
}


def _escape_html(text: str) -> str:
    return _html.escape(text, quote=True)


# ASCII no URL may hold as-is. Anything else passes through: `%` so an escaped
# href stays as it is, and non-ASCII because HTML takes IRIs -- encoding it
# would break a Greek hostname.
_URL_UNSAFE = str.maketrans({c: f"%{ord(c):02X}"
                             for c in ' "<>\\^`{|}' + "".join(map(chr, range(32))) + chr(127)})


def _safe_href(href: str) -> str:
    """A link target with the characters no URL may hold percent-encoded.

    Word stores a hyperlink as typed. The first real book's links into Perseus
    carry raw `\\` and `|` in their queries (Beta Code accents); EPUBCheck
    rejects each one (RSC-020), and a PDF link annotation holds the same bad
    URI. Encoding them does not change where the link goes.
    """
    return href.strip().translate(_URL_UNSAFE)


def _media_src(ref: dict) -> str:
    """`media/<sha256>.<ext>` for a figure's mediaRef.

    Content-addressed rather than named: the src is enough for any renderer to
    pull the bytes back out of CAS, so the HTML carries no path into a work
    directory that will not exist by the time it is rendered.
    """
    digest = ref.get("hash")
    if not digest:
        return ""
    ext = MEDIA_EXTENSIONS.get(ref.get("mediaType", ""), "bin")
    return f"media/{digest}.{ext}"


def _render_inline(content: list) -> str:
    """Render AST inline content to HTML."""
    parts = []
    for node in content:
        ntype = node.get("type", "text")

        if ntype == "text":
            text = _escape_html(node.get("text", ""))
            # Apply marks
            marks = node.get("marks", [])
            for mark in marks:
                mtype = mark.get("type", "")
                if mtype == "emphasis":
                    text = f"<em>{text}</em>"
                elif mtype == "strong":
                    text = f"<strong>{text}</strong>"
                elif mtype == "smallCaps":
                    text = f'<span class="small-caps">{text}</span>'
                elif mtype == "superscript":
                    text = f"<sup>{text}</sup>"
                elif mtype == "subscript":
                    text = f"<sub>{text}</sub>"
                elif mtype == "code":
                    text = f"<code>{text}</code>"
                elif mtype == "link":
                    href = (mark.get("attrs") or {}).get("href", "")
                    text = f'<a href="{_escape_html(_safe_href(href))}">{text}</a>'
            parts.append(text)

        elif ntype == "emphasis":
            p = _render_inline(node.get("content", []))
            parts.append(f"<em>{p}</em>")

        elif ntype == "strong":
            p = _render_inline(node.get("content", []))
            parts.append(f"<strong>{p}</strong>")

        elif ntype == "hardBreak":
            parts.append("<br/>")

        elif ntype == "codeInline":
            parts.append(f"<code>{_escape_html(node.get('text', ''))}</code>")

        elif ntype == "superscript":
            parts.append(f"<sup>{_render_inline(node.get('content', []))}</sup>")

        elif ntype == KEEP:
            parts.append(f'<span class="keep">{_render_inline(node["content"])}{node.get("after", "")}</span>')

        else:
            parts.append(_escape_html(str(node.get("text", ""))))

    return "".join(parts)


# A paragraph's last two words are set as one unbreakable unit in print, so its
# last line can never hold a single word (a runt: 264 pages of the first real
# book). `KEEP` is a render-only inline node -- never in an AST -- wrapping the
# words in `<span class="keep">` (`white-space: nowrap`). The characters are the
# paragraph's own, so the text stream the integrity gate reads is unchanged.
# The pair may span text runs (Word splits runs at every formatting or revision
# boundary, so a last word is often a run of its own); each piece keeps its
# marks. Capped well under a line so the pair always fits one: Greek pairs of
# 25-28 characters were common, a body line holds about 60. A table cell gets a
# smaller cap: auto table layout widens a column to fit an unbreakable pair
# rather than overflow it, so the cap bounds how far a column can move.
KEEP = "_keep"
KEEP_TAIL_CHARS = 40
KEEP_CELL_CHARS = 16


def _keep_tail(content: list, cap: int = KEEP_TAIL_CHARS, after: str = "") -> list:
    """`content` with its final two words wrapped in one KEEP node, when they sit
    in its trailing text runs and together are at most `cap` long.

    `after` is markup that must stay on the last word's line -- a paragraph's
    note calls: rendered after the text, a line could break before them and
    leave the number alone on the last line (5 runts in the first real book).
    It goes inside the KEEP node. When the pair is longer than `cap` -- a URL,
    a long citation -- the last three quarters of `cap` characters are kept
    instead, so the last line still holds a real stretch of text rather than a
    lone fragment (a URL breaks at its slashes). `content` itself comes back
    when nothing was kept, and the caller then places `after` itself."""
    first = len(content)
    while first and content[first - 1].get("type", "text") == "text":
        first -= 1
    runs = content[first:]
    joined = "".join(run.get("text", "") for run in runs)
    end = len(joined.rstrip())
    last = end
    while last and not joined[last - 1].isspace():
        last -= 1                               # start of the last word
    i = last
    while i and joined[i - 1].isspace():
        i -= 1                                  # end of the word before it
    start = i
    while start and not joined[start - 1].isspace():
        start -= 1                              # start of the word before it
    if i and end - start <= cap:
        pass                                    # the pair
    elif end > cap:
        start = end - cap * 3 // 4              # a long pair or word: its last stretch
    elif after and last < end:
        start = last                            # one short word, holding its calls
    else:
        return content
    if after:
        end = len(joined)                       # no break before the calls either
    head: list = []
    kept: list = []
    trail: list = []
    pos = 0
    for run in runs:
        text = run.get("text", "")
        lo, hi = pos, pos + len(text)
        for a, b, into in ((lo, min(hi, start), head), (max(lo, start), min(hi, end), kept),
                           (max(lo, end), hi, trail)):
            if b > a:
                into.append({**run, "text": text[a - lo:b - lo]})
        pos = hi
    return content[:first] + head + [{"type": KEEP, "content": kept, "after": after}] + trail


def _render_table(node: dict, notes: "EpubNotes | None" = None) -> str:
    """Render an AST table to HTML."""
    parts = ['<table>']
    caption = (node.get("attrs") or {}).get("caption", "")
    if caption:
        parts.append(f'<caption>{_escape_html(caption)}</caption>')

    for row in node.get("content", []):
        is_header = (row.get("attrs") or {}).get("header", False)
        tag = "th" if is_header else "td"
        parts.append("<tr>")
        for cell in row.get("content", []):
            colspan = (cell.get("attrs") or {}).get("colspan", 1)
            parts.append(f'<{tag} colspan="{colspan}">{_render_content(cell.get("content", []), notes, KEEP_CELL_CHARS)}</{tag}>')
        parts.append("</tr>")
    parts.append("</table>")
    return "\n".join(parts)


# A footnote longer than this (characters of text) is set in pieces of at most
# NOTE_PIECE_CHARS, split at a sentence end. weasyprint 62 cannot break a note
# across pages, so a note nearly a page long filled the whole footnote area and
# left the body one line: the first real book had four such pages, each an
# orphan weasyprint cannot avoid (it will not leave a page with no body text).
# Pieces it can carry over one at a time, and NOTE_AREA_MAX keeps a share of
# every page for the body. A piece is far shorter than that share, so no piece
# can run past the type area -- which a cap on unsplit notes did, measured.
NOTE_SPLIT_CHARS = 1200
NOTE_PIECE_CHARS = 900
NOTE_AREA_MAX = "60%"

# Where a long note may break, best first: after a sentence, after a clause.
_NOTE_BREAKS = (". ", "; ", "\u00b7 ", "! ", "? ", ": ")


def _inline_chars(content: list) -> int:
    return sum(len(node.get("text", "")) for node in content)


def _note_cut(text: str, room: int) -> int:
    """Index to cut `text` at, within its first `room` characters: after the
    last sentence or clause end, else the last space. 0 when nothing fits."""
    if room <= 0:
        # The piece is already full (a run with no break in it was kept whole).
        # A negative room also made `room // 3` negative, so "not found" (-1)
        # passed the test below and cut after the run's first character.
        return 0
    window = text[:room + 1]
    for mark in _NOTE_BREAKS:
        at = window.rfind(mark)
        if at >= 0 and at > room // 3:
            return at + len(mark)
    at = window.rfind(" ")
    return at + 1 if at > 0 else 0


def _first_break(text: str) -> int:
    """Index just past the earliest sentence end, clause end or space in `text`;
    0 when it has none. For a piece already over its limit: end it as soon as
    the text allows rather than never (a URL with no break in it overfills a
    piece, and the next run then had no room at all)."""
    cuts = [at + len(mark) for mark in _NOTE_BREAKS if (at := text.find(mark)) >= 0]
    space = text.find(" ")
    if space >= 0:
        cuts.append(space + 1)
    return min(cuts, default=0)


def _split_note(content: list, limit: int) -> list[list]:
    """A note's inline content cut into pieces of at most `limit` characters.

    Cuts fall only inside text runs, which keep their marks, so an italic
    quotation split across two pieces stays italic in both. The whitespace at
    a cut is dropped: each piece renders with a leading space (see
    `PrintNotes.render`), which is what separates it from the one before --
    the text stream is unchanged.
    """
    pieces: list[list] = []
    current: list = []
    size = 0
    for node in content:
        if node.get("type") != "text":
            current.append(node)
            size += len(node.get("text", ""))
            continue
        text = node.get("text", "")
        while size + len(text) > limit:
            cut = _note_cut(text, limit - size)
            if cut > 0 and current and current[-1].get("type") == "hardBreak" \
                    and " " not in text[:cut].strip():
                # Right after a forced line break the piece's last line is its
                # own: one word there is a runt no KEEP can reach (the first real
                # book, p. 276). Take one more word, a little over the limit.
                more = text.find(" ", cut)
                cut = more + 1 if more > 0 else len(text)
            if cut <= 0:
                # No break inside this run in the room left. The boundary before
                # it is a break only where the text already has whitespace there:
                # a piece opens with a space, so cutting "(" | "http://..." (a
                # link is a run of its own) put a space in the book that was not
                # there, and the integrity gate refused the first real book.
                ends_in_space = bool(current) and current[-1].get("text", "")[-1:].isspace()
                if current and (ends_in_space or text[:1].isspace()):
                    pieces.append(current)
                    current, size = [], 0
                    continue
                # No clean boundary either: end the piece at the first break the
                # run offers, even if that leaves it over the limit.
                cut = _first_break(text)
                if cut <= 0:
                    break   # no break anywhere in the run: keep it whole
            head, text = text[:cut].rstrip(), text[cut:].lstrip()
            if head:
                current.append({**node, "text": head})
            if current:
                pieces.append(current)
            current, size = [], 0
        if text:
            current.append({**node, "text": text})
            size += len(text)
    if current:
        pieces.append(current)
    return pieces or [[]]


class PrintNotes:
    """Footnote numbering and markup for the print flavour of `_render_content`.

    The call is our own element, `<span class="note-call" data-n>`, drawn by CSS
    (`::after { content: attr(data-n) }`), and the note carries the same number
    for its marker. weasyprint's generated call cannot be restyled per note --
    it belongs to the paragraph -- so its counter could not skip the pieces of a
    split note: each piece printed a call of its own ("5555"). CSS-generated
    content is not in the HTML's text, so the integrity gate reads the same
    stream as before. Numbered through the whole book, like the EPUB.
    """

    def __init__(self) -> None:
        self.count = 0
        self.seq = 0

    def _seq(self) -> int:
        self.seq += 1
        return self.seq

    def render(self, node: dict) -> str:
        return "".join(self.render_parts(node))

    def render_parts(self, node: dict) -> tuple[str, str]:
        """The note's call and its body (every piece), separately."""
        self.count += 1
        n = self.count
        content = node.get("content", [])
        pieces = (_split_note(content, NOTE_PIECE_CHARS)
                  if _inline_chars(content) > NOTE_SPLIT_CHARS else [content])
        # The leading space in each span is the text-stream separator between a
        # paragraph and its note, and between pieces: without it the integrity
        # gate reads "text.Note" against the source's "text. Note". In the
        # footnote area it collapses at line start.
        first, *rest = pieces
        # `data-seq`: every piece's place in the book, for
        # paginate_stage.notes_in_document_order.
        return (f'<span class="note-call" data-n="{n}"></span>',
                f'<span class="footnote" data-n="{n}" data-seq="{self._seq()}"> {_render_inline(_keep_tail(first))}</span>'
                + "".join(f'<span class="footnote footnote-cont" data-seq="{self._seq()}"> '
                          f'{_render_inline(_keep_tail(piece))}</span>'
                          for piece in rest))


class EpubNotes:
    """Footnote numbering for the EPUB flavour of `_render_content`.

    Print leaves numbering to the renderer (`float: footnote`); a reading system
    has no footnote area, so the EPUB carries the call itself: a `noteref` link
    in the citing paragraph and the note as an `aside epub:type="footnote"`
    right after that paragraph. Readers that support it show the aside as a
    pop-up; the rest show it where it sits. Placing it after the paragraph --
    not in a list at the end of the chapter -- keeps the text in the AST's order,
    so the EPUB's integrity check reads the same stream as the print gate's,
    minus the call numbers this class generates. Numbered through the whole
    book, like print (`counter-reset: footnote` on `body`).
    """

    NOTEREF = "noteref"

    def __init__(self) -> None:
        self.count = 0

    def call(self) -> tuple[str, str]:
        """The next note's call link and its aside's opening tag."""
        self.count += 1
        n = self.count
        return (f'<a epub:type="{self.NOTEREF}" id="fnref-{n}" href="#fn-{n}">{n}</a>',
                f'<aside epub:type="footnote" id="fn-{n}">')

    def aside(self, opening: str, node: dict) -> str:
        return f'{opening}<p>{_render_inline(node.get("content", []))}</p></aside>'


def _render_content(content: list, notes: "EpubNotes | PrintNotes | None" = None,
                    cap: int = KEEP_TAIL_CHARS) -> str:
    """Render AST block content to HTML.

    `notes` selects the EPUB flavour (linked footnotes, see `EpubNotes`); every
    other node renders identically in both, which is what lets the EPUB claim
    the text the print gate verified. Print numbering (`PrintNotes`) runs across
    whatever one call renders; `ast_to_html` passes one through the whole book.
    """
    if notes is None:
        notes = PrintNotes()
    parts = []
    absorbed = 0
    for index, node in enumerate(content):
        if absorbed:
            absorbed -= 1
            continue
        ntype = node.get("type", "unknown")

        if ntype == "paragraph":
            role = (node.get("attrs") or {}).get("role", "normal")
            cls = f"paragraph {role}" if role != "normal" else "paragraph"
            # The footnotes that follow a paragraph (ingest places each note
            # after the paragraph that cites it) are rendered INSIDE it, before
            # `</p>`, so the call number weasyprint generates sits on the
            # paragraph's last line. Rendered after the `</p>`, the call got an
            # anonymous line of its own -- a lone "1" under every cited
            # paragraph, all 477 of them in the first real book. Text order is
            # unchanged, so the integrity gate sees the same stream.
            cited = []
            for following in content[index + 1:]:
                if following.get("type") != "footnote":
                    break
                cited.append(following)
            absorbed = len(cited)
            inline = node.get("content", [])
            if isinstance(notes, PrintNotes):
                # Print only (KEEP): a reflowing EPUB has no fixed last line to
                # protect. All the calls come first, then the note bodies: the
                # calls hold no text, so the text stream is unchanged.
                rendered = [notes.render_parts(note) for note in cited]
                calls = "".join(call for call, _ in rendered)
                kept = _keep_tail(inline, cap, after=calls)
                text = _render_inline(kept) + ("" if kept is not inline else calls)
                parts.append(f'<p class="{cls}">{text}{"".join(body for _, body in rendered)}</p>')
            else:
                text = _render_inline(inline)
                calls = [notes.call() for _ in cited]
                refs = "".join(ref for ref, _ in calls)
                asides = "".join(notes.aside(opening, note) for (_, opening), note in zip(calls, cited))
                parts.append(f'<p class="{cls}">{text}{refs}</p>{asides}')

        elif ntype == "heading":
            level = (node.get("attrs") or {}).get("level", 2)
            inline = node.get("content", [])
            if isinstance(notes, PrintNotes):
                inline = _keep_tail(inline)             # a heading's last line too
            parts.append(f'<h{level}>{_render_inline(inline)}</h{level}>')

        elif ntype == "blockquote":
            parts.append(f'<blockquote>{_render_content(node.get("content", []), notes)}</blockquote>')

        elif ntype == "epigraph":
            source = (node.get("attrs") or {}).get("source", "")
            inner = _render_content(node.get("content", []), notes)
            parts.append(f'<blockquote class="epigraph">{inner}')
            if source:
                parts.append(f'<footer>{_escape_html(source)}</footer>')
            parts.append("</blockquote>")

        elif ntype == "verse":
            parts.append('<div class="verse">')
            for line in node.get("content", []):
                parts.append(f'<p class="verse-line">{_render_inline(line.get("content", []))}</p>')
            parts.append("</div>")

        elif ntype == "sceneBreak":
            ornament = (node.get("attrs") or {}).get("ornament", "dinkus")
            parts.append(f'<hr class="scene-break" data-ornament="{ornament}" />')

        elif ntype == "code":
            lang = (node.get("attrs") or {}).get("language", "")
            parts.append(f'<pre class="code-block" data-language="{lang}">{_escape_html(node.get("content", ""))}</pre>')

        elif ntype == "dialogue":
            speaker = (node.get("attrs") or {}).get("speaker", "")
            inner = _render_content(node.get("content", []), notes)
            parts.append(f'<div class="dialogue" data-speaker="{_escape_html(speaker)}">{inner}</div>')

        elif ntype == "list":
            list_type = (node.get("attrs") or {}).get("listType", "unordered")
            tag = "ol" if list_type == "ordered" else "ul"
            parts.append(f'<{tag}>')
            for item in node.get("content", []):
                parts.append(f'<li>{_render_content(item.get("content", []), notes)}</li>')
            parts.append(f'</{tag}>')

        elif ntype == "figure":
            attrs = node.get("attrs", {})
            caption = attrs.get("caption", "")
            src = _media_src(attrs.get("mediaRef") or {})
            parts.append('<figure>')
            # The <img> was missing entirely: every figure rendered as an empty
            # box with a caption under it. Nothing caught it, because a picture
            # contributes no text for the integrity gate to miss.
            if src:
                parts.append(f'<img src="{src}" alt="{_escape_html(attrs.get("altText", ""))}"/>')
            parts.append(f'<figcaption>{_escape_html(caption)}</figcaption>' if caption else '')
            parts.append('</figure>')

        elif ntype == "footnote":
            # A note with no paragraph before it (after a heading or a figure).
            # An inline element at block position on purpose: `float: footnote`
            # (CSS Generated Content for Paged Media) moves it into the page's
            # footnote area and numbers the call itself. Rendering it as a block
            # would print the note inline in the text where it happens to sit.
            if isinstance(notes, PrintNotes):
                parts.append(notes.render(node))
            else:
                ref, opening = notes.call()
                parts.append(f'<p class="noteref-only">{ref}</p>{notes.aside(opening, node)}')

        elif ntype == "sidebar":
            parts.append(f'<aside class="sidebar">{_render_content(node.get("content", []), notes)}</aside>')

        elif ntype == "pageBreak":
            parts.append('<div class="page-break"></div>')

        elif ntype in ("halfTitle", "titlePage", "copyrightPage", "dedication", "toc", "foreword",
                       "preface", "acknowledgments", "prologue", "epilogue", "afterword",
                       "appendix", "notes", "bibliography", "index", "aboutTheAuthor", "alsoBy", "colophon"):
            role = ntype
            parts.append(f'<div class="{role}">{_render_content(node.get("content", []), notes)}</div>')

        elif ntype == "table":
            parts.append(_render_table(node, notes))

        else:
            parts.append(f'<!-- unknown node type: {ntype} -->')

    return "\n".join(parts)


def ast_to_html(ast: dict) -> str:
    """Convert a Book AST into a flat typescript-like HTML document.

    This is deliberately naive -- the tracer bullet validates the contract,
    not the quality. Production will use XSweet.

    `extract` and `paginate` (and, for their measured-pagemap hint,
    `idml_stage.py` and `typst_stages.py`) must all call this SAME function
    object -- see the module docstring.
    """
    parts = []
    notes = PrintNotes()

    # Title from metadata
    title = (ast.get("metadata") or {}).get("title", "Untitled")

    # Process front matter
    front_matter = ast.get("frontMatter") or []
    for item in front_matter:
        parts.append(f'<div class="front-matter {item.get("type", "unknown")}">')
        parts.append(_render_content(item.get("content", []), notes))
        parts.append("</div>")

    # Process body (chapters)
    body = ast.get("body", [])
    for chapter in body:
        ctype = chapter.get("type", "unknown")
        attrs = chapter.get("attrs", {})
        cid = attrs.get("id", f"{ctype}-{attrs.get('number', '?')}")
        title_text = attrs.get("title", f"Chapter {attrs.get('number', '?')}")

        parts.append(f'<div class="{ctype}" id="{cid}" data-number="{attrs.get("number", "")}">')
        title_html = _render_inline(_keep_tail([{"type": "text", "text": title_text}]))
        parts.append(f'<h1 class="chapter-title">{title_html}</h1>')
        parts.append(_render_content(chapter.get("content", []), notes))
        parts.append("</div>")

    # Process back matter
    back_matter = ast.get("backMatter") or []
    for item in back_matter:
        parts.append(f'<div class="back-matter {item.get("type", "unknown")}">')
        parts.append(_render_content(item.get("content", []), notes))
        parts.append("</div>")

    return HTML_TEMPLATE.format(title=_escape_html(title), body="\n".join(parts))


# ── the EPUB's sections ─────────────────────────────────────────────────────

MATTER_LABELS = {
    "halfTitle": "Half Title", "titlePage": "Title Page", "copyrightPage": "Copyright",
    "dedication": "Dedication", "epigraph": "Epigraph", "toc": "Contents",
    "foreword": "Foreword", "preface": "Preface", "acknowledgments": "Acknowledgments",
    "introduction": "Introduction", "prologue": "Prologue", "epilogue": "Epilogue",
    "afterword": "Afterword", "appendix": "Appendix", "notes": "Notes",
    "glossary": "Glossary", "bibliography": "Bibliography", "index": "Index",
    "aboutTheAuthor": "About the Author", "alsoBy": "Also By", "colophon": "Colophon",
}


def ast_to_epub_sections(ast: dict) -> list[dict]:
    """The book as EPUB content documents: one per front-matter item, chapter and
    back-matter item, in the order `ast_to_html` renders them.

    Each is `{"id", "title", "matter", "body"}`, `body` being the markup inside
    `<body>`. The blocks come from the same `_render_content` the print gate
    verified, in its EPUB flavour (`EpubNotes`); only the footnotes differ.
    The EPUB writer used to carry a renderer of its own, and it dropped every
    footnote, table, figure, sidebar and front/back-matter section and every
    mark, and moved words around inline elements: it held 76% of the first real
    book's text.
    """
    notes = EpubNotes()
    sections: list[dict] = []

    def add(matter: str, title: str, body: str) -> None:
        sections.append({"id": f"s{len(sections) + 1:03d}", "title": title,
                         "matter": matter, "body": body})

    for item in ast.get("frontMatter") or []:
        kind = item.get("type", "unknown")
        title = (item.get("attrs") or {}).get("title") or MATTER_LABELS.get(kind, kind)
        add("frontmatter", title, f'<section class="front-matter {kind}">'
            f'{_render_content(item.get("content", []), notes)}</section>')

    for chapter in ast.get("body", []):
        ctype = chapter.get("type", "unknown")
        attrs = chapter.get("attrs", {})
        title = attrs.get("title", f"Chapter {attrs.get('number', '?')}")
        add("bodymatter", title,
            f'<section class="{ctype}" id="{_escape_html(str(attrs.get("id", "")))}">'
            f'<h1 class="chapter-title">{_escape_html(title)}</h1>'
            f'{_render_content(chapter.get("content", []), notes)}</section>')

    for item in ast.get("backMatter") or []:
        kind = item.get("type", "unknown")
        title = (item.get("attrs") or {}).get("title") or MATTER_LABELS.get(kind, kind)
        add("backmatter", title, f'<section class="back-matter {kind}">'
            f'{_render_content(item.get("content", []), notes)}</section>')

    return sections


# ── emit_css (moved from design_compile_stage.py) ───────────────────────────

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


# DesignSpec `startsOn` -> CSS `break-before`. NOT the legacy `page-break-before`:
# CSS 2 gave that property only left/right, and weasyprint drops `recto` there
# without a word. Every chapter and front/back-matter section asked for a recto
# start that way; none got one. Chapters still opened on a new page only because
# their named `@page` changed; the first real book's contents page ran on from
# its epigraph mid-page.
BREAK_BEFORE = {"recto": "recto", "verso": "verso", "any": "page"}


def emit_css(designspec: dict, bleed_mm: float = 0.0) -> str:
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

    `design-compile` and `paginate`'s CSS fallback must both call this SAME
    function object -- see the module docstring.
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

    body_font_family = (typography.get("bodyFont") or {}).get("family", "GFS Didot")
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
    starts_on = BREAK_BEFORE[chapter_openings.get("startsOn", "recto")]
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
        # rendering.KEEP: a paragraph's last two words, never split (no runts).
        ".keep {",
        "  white-space: nowrap;",
        "}",
        "",
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
    # renderer lifts the span out of the text flow into the page's footnote area.
    # The numbers are the renderer's own (`PrintNotes`: `data-n` on the call and
    # on the note), not weasyprint's counter, so the pieces of a long note carry
    # none. The area is `@footnote` -- this read `@footnotes`, which matches no
    # area, so the rule above the notes and its spacing were never drawn -- and
    # capped (NOTE_AREA_MAX) so notes cannot squeeze a page's text to one line.
    # No `footnote-policy: line`: it kept notes on their call's page by moving
    # the call's line over -- and while any note was waiting to carry over,
    # weasyprint 70 moved EVERY later call line, leaving 23 pages of the first
    # real book with one line of text and 27 widows. The default lets a note
    # carry over; paginate_stage.notes_in_document_order keeps them in order.
    lines.extend([
        "@page { @footnote { border-top: 0.5pt solid currentColor; padding-top: 0.4em; "
        f"max-height: {NOTE_AREA_MAX}; }} }}",
        "span.footnote {",
        "  float: footnote;",
        "  footnote-style-position: outside;",
        f"  font-size: {body_size * 0.82}pt;",
        "  text-align: left;",
        "  text-indent: 0;",
        "}",
        "::footnote-call { content: ''; }",
        "span.note-call::after {",
        "  content: attr(data-n);",
        "  vertical-align: super;",
        "  font-size: 0.7em;",
        "  line-height: 0;",
        "}",
        "::footnote-marker {",
        "  content: attr(data-n) '. ';",
        "  font-weight: normal;",
        "}",
        "span.footnote-cont::footnote-marker { content: ''; }",
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

    # Named pages for chapter openings
    lines.extend([
        "@page chapter-opening {",
        f"  @top-left {{ content: none; }}",
        f"  @top-right {{ content: none; }}",
        "}",
        "",
    ])

    return "\n".join(lines)


__all__ = ["ast_to_html", "ast_to_epub_sections", "emit_css", "EpubNotes", "PrintNotes"]
