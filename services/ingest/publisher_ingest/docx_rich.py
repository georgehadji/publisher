"""
Rich DOCX extraction: runs, tables, images, footnotes.

WHY THIS EXISTS. `docx_to_ast` flattened every block to
`{"type":"paragraph","content":[{"type":"text","text": para.text}]}`. That is
lossless for *prose characters* -- which is exactly what its no-loss
post-condition checks -- and lossy for everything else. Each loss was invisible
to every gate downstream:

  * `para.text` concatenates runs and discards `w:rPr`, so italics and bold
    never entered the AST at all.
  * Tables were unrolled cell-by-cell into loose paragraphs, deliberately, on
    the grounds that nothing downstream could lay a table out.
  * Images live in `w:drawing`, which carries no `w:t`, so they were dropped
    without tripping the text check -- a picture has no text to miss.
  * Footnote bodies live in a separate part (`word/footnotes.xml`) that
    python-docx does not expose at all. Same silence, same reason.

The AST schema already models all four (`$defs`: table, figure, mediaRef,
footnote, mark). Only the extractor was missing.

XML rather than python-docx for the run walk: `Paragraph.runs` skips runs nested
in `w:hyperlink`, and exposes neither footnote references nor drawings.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Optional

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"

# Wrappers whose content is ordinary document text: an accepted-or-pending
# insertion, the landing side of a move, a content control, smart tags, custom
# XML, a simple field's result, bidi overrides. Word writes all of these into
# real manuscripts (corpus/word/), and a walk that only knew `w:r` and
# `w:hyperlink` dropped every character inside them -- silently, because the
# no-loss check used to be fed by that same walk.
TRANSPARENT = frozenset(f"{W}{t}" for t in (
    "ins", "moveTo", "smartTag", "customXml", "fldSimple", "bdo", "dir",
))
# Text that is in the XML but not in the document: a tracked deletion, the
# source side of a move, and the legacy VML duplicate Word writes of every
# text box (`mc:Fallback` repeats the `mc:Choice` content verbatim).
HIDDEN = frozenset({f"{W}del", f"{W}moveFrom", f"{MC}Fallback"})

# `w:noBreakHyphen` is a hyphen the author asked never to break at ("well-known"
# as one word at a line end). U+2011 keeps that; a renderer missing the glyph
# falls back to another font's hyphen rather than printing nothing.
NO_BREAK_HYPHEN = "\u2011"

# `mediaRef.mediaType` in ast.schema.json -- the images a renderer can draw.
# EMF/WMF (Word's own vector formats) are not among them.
MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/svg+xml", "image/tiff"})

# Signature of the media sink: (bytes, media_type, original_name) -> sha256 hex.
# The caller owns storage; this module never decides where image bytes live.
MediaSink = Callable[[bytes, str, str], str]

# Word writes these two synthetic footnotes into every document that has any.
# They are the rule drawn above the footnote area, not content.
SYNTHETIC_FOOTNOTES = {"separator", "continuationSeparator"}

# `w:rPr` toggles -> AST mark types. Word writes `<w:b/>` for on and
# `<w:b w:val="0"/>` for explicitly off, so presence alone is not enough.
RUN_MARKS = {
    f"{W}b": "strong",
    f"{W}i": "emphasis",
    f"{W}smallCaps": "smallCaps",
    f"{W}u": "underline",
    f"{W}strike": "strikethrough",
}


class MediaNotStorable(RuntimeError):
    """An image was found but the caller supplied nowhere to put it.

    Raised rather than dropped: a manuscript that silently loses its figures is
    the exact failure this module exists to end.
    """


class UnsupportedContent(RuntimeError):
    """The manuscript holds content ingest cannot represent. Raised, never dropped."""


# `w:sym` (W9): a character set in a symbol font, by its code in THAT font --
# Word's AutoCorrect writes "-->" as Wingdings 0xE0, and an author picking from
# Insert > Symbol gets Symbol-font codes. The walk used to skip the element, and
# the oracle never counted it, so the character vanished with every check green.
# Symbol fonts are addressed at 0xF0xx (or plain 0x00xx); the low byte is the
# code. Symbol is Adobe's published encoding. Wingdings has no standard Unicode
# table; only the arrows are mapped, to the arrows they mean (U+2190-2193)
# rather than the rare sans-serif arrows at U+1F850, which almost no text face
# draws. Anything else is refused by name, so the table grows from real books.
_SYMBOL_LETTERS = dict(zip(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖαβχδεφγηιϕκλμνοπθρστυϖωξψζ",
))
SYMBOL_FONTS: dict[str, dict[int, str]] = {
    "Symbol": {
        **{ord(c): c for c in " !#%&(),./0123456789:;<=>?[]_{|}+"},
        **{ord(k): v for k, v in _SYMBOL_LETTERS.items()},
        0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−", 0x40: "≅", 0x5C: "∴",
        0x5E: "⊥", 0x7E: "∼", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞",
        0xA6: "ƒ", 0xA7: "♣", 0xA8: "♦", 0xA9: "♥", 0xAA: "♠", 0xAB: "↔", 0xAC: "←",
        0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°", 0xB1: "±", 0xB2: "″", 0xB3: "≥",
        0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡",
        0xBB: "≈", 0xBC: "…", 0xC0: "ℵ", 0xC1: "ℑ", 0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗",
        0xC5: "⊕", 0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇", 0xCB: "⊄",
        0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉", 0xD0: "∠", 0xD1: "∇", 0xD5: "∏",
        0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔", 0xDC: "⇐",
        0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓", 0xE0: "◊", 0xE1: "〈", 0xE5: "∑", 0xF1: "〉",
        0xF2: "∫",
    },
    "Wingdings": {0xDF: "←", 0xE0: "→", 0xE1: "↑", 0xE2: "↓"},
}


def sym_char(sym: etree._Element) -> str:
    """The Unicode character a `w:sym` stands for, or UnsupportedContent."""
    font = sym.get(f"{W}font") or ""
    code = int(sym.get(f"{W}char") or "0", 16)
    table = SYMBOL_FONTS.get(font)
    if table is None and code < 0xF000:
        return chr(code)   # an ordinary font: the code is the character itself
    char = (table or {}).get(code & 0xFF)
    if char is None:
        raise UnsupportedContent(
            f"a {font or 'symbol-font'} symbol (code {code:04X}) has no Unicode mapping; "
            "replace it in Word with the character it stands for"
        )
    return char


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _toggle_on(el: Optional[etree._Element]) -> bool:
    if el is None:
        return False
    return el.get(f"{W}val") not in ("0", "false", "off")


def _marks_for(rpr: Optional[etree._Element]) -> list[dict]:
    if rpr is None:
        return []
    marks = [{"type": m} for tag, m in RUN_MARKS.items() if _toggle_on(rpr.find(tag))]
    vert = rpr.find(f"{W}vertAlign")
    if vert is not None and vert.get(f"{W}val") in ("superscript", "subscript"):
        marks.append({"type": vert.get(f"{W}val")})
    return marks


def _text_node(text: str, marks: list[dict]) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def _merge_text(nodes: list[dict]) -> list[dict]:
    """Coalesce adjacent text nodes carrying identical marks.

    Word splits a single word across runs whenever the spell-checker or a
    tracked revision touched it, so a naive walk emits `["Publi", "sher"]`.
    Downstream consumers (pandoc ICML, the EPUB writer) wrap each fragment
    separately, which shows up in InDesign as separate style ranges.
    """
    merged: list[dict] = []
    for node in nodes:
        if (
            merged
            and node.get("type") == "text"
            and merged[-1].get("type") == "text"
            and merged[-1].get("marks") == node.get("marks")
        ):
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + node["text"]}
        else:
            merged.append(node)
    return merged


def _figure_from_run(run: etree._Element, part, sink: Optional[MediaSink]) -> Optional[dict]:
    """Build a `figure` node from a run containing a DrawingML picture."""
    blip = run.find(f".//{A}blip")
    if blip is None:
        return None
    rid = blip.get(f"{R}embed")
    if not rid:
        return None
    try:
        image_part = part.rels[rid].target_part
    except KeyError:
        # A relationship id the package does not define: the picture is already
        # broken in Word. Say so rather than emit a mediaRef pointing at nothing.
        raise MediaNotStorable(f"image relationship {rid!r} is not in the package")

    if sink is None:
        raise MediaNotStorable(
            "the manuscript contains images but no media sink was supplied; "
            "pass store_media= so the bytes get a home instead of being dropped"
        )

    name = str(image_part.partname).rsplit("/", 1)[-1]
    if image_part.content_type not in MEDIA_TYPES:
        # Kept, it would be a figure no renderer can draw and an AST the schema
        # rejects -- the first real book carried an 8.7 MB EMF diagram.
        raise UnsupportedContent(
            f"image {name!r} is {image_part.content_type}, which nothing here can "
            f"render; re-save it in Word as PNG or JPEG ({', '.join(sorted(MEDIA_TYPES))})"
        )
    blob = image_part.blob
    attrs: dict = {
        "mediaRef": {
            "hash": sink(blob, image_part.content_type, name),
            "mediaType": image_part.content_type,
            "originalName": name,
        }
    }
    doc_pr = run.find(f".//{WP}docPr")
    if doc_pr is not None and (doc_pr.get("descr") or "").strip():
        attrs["altText"] = doc_pr.get("descr").strip()
    return {"type": "figure", "attrs": attrs}


def diagram_texts(el: etree._Element, part) -> list[str]:
    """The text of every SmartArt diagram under `el`, one string per paragraph.

    A SmartArt's words are not in document.xml at all: the drawing holds only
    `dgm:relIds`, pointing at a data part whose `a:t` runs are the text. So a
    walk of the document, and an oracle that scans it, both miss them -- the
    first real book lost two diagrams' labels with every check green.
    """
    texts: list[str] = []
    for ids in el.iter(f"{DGM}relIds"):
        try:
            data = part.rels[ids.get(f"{R}dm")].target_part.blob
        except KeyError:
            raise UnsupportedContent("a SmartArt diagram's data part is missing from the package")
        for para in etree.fromstring(data).iter(f"{A}p"):
            text = "".join(t.text or "" for t in para.iter(f"{A}t")).strip()
            if text:
                texts.append(text)
    return texts


def _inline_runs(
    para: etree._Element, part, sink, footnote_refs: Optional[list],
    footnotes: Optional[dict] = None,
) -> tuple[list[dict], str]:
    """Inline nodes for one `w:p`, plus the DOCX text it drew them from.

    Returns `(nodes, source_text)`. The two differ: `source_text` holds only
    characters that were in the DOCX (`w:t`, `w:tab`), while `nodes` may also
    carry a footnote marker this extractor synthesised. Keeping them apart
    matters -- `source_text` is what the no-loss post-condition checks, and a
    marker we invented is not text the manuscript can be said to have lost.

    `footnote_refs`, when given, collects `(key, number)` for every footnote or
    endnote reference met; the caller turns those into sibling `footnote` blocks.
    Figures and text boxes come back wrapped in `{"__block__": ...}` for the
    caller to lift out -- the schema has no inline image or sidebar node.
    """
    nodes: list[dict] = []
    source: list[str] = []

    def walk_run_children(run: etree._Element, children, marks: list[dict]) -> None:
        for child in children:
            tag = child.tag
            if tag == f"{W}t":
                nodes.append(_text_node(child.text or "", marks))
                source.append(child.text or "")
            elif tag == f"{W}tab":
                nodes.append(_text_node(" ", marks))
                source.append(" ")
            elif tag == f"{W}noBreakHyphen":
                nodes.append(_text_node(NO_BREAK_HYPHEN, marks))
                source.append(NO_BREAK_HYPHEN)
            elif tag == f"{W}sym":
                char = sym_char(child)
                nodes.append(_text_node(char, marks))
                source.append(char)
            elif tag in (f"{W}br", f"{W}cr"):
                nodes.append({"type": "hardBreak"})
            elif tag in (f"{W}footnoteReference", f"{W}endnoteReference") and footnote_refs is not None:
                # Reference recorded, NO marker text emitted. Every target
                # engine numbers its own call -- Typst `#footnote`, CSS
                # `::footnote-call`, InDesign's footnote options -- so a literal
                # "1" in the text would print twice. It would also be text on
                # the AST side that no DOCX text matches, which is precisely
                # what the integrity gate is built to reject.
                kind = "fn" if tag == f"{W}footnoteReference" else "en"
                footnote_refs.append((f"{kind}:{child.get(f'{W}id')}", len(footnote_refs) + 1))
            elif tag in (f"{W}drawing", f"{W}pict", f"{W}object"):
                figure = _figure_from_run(run, part, sink)
                if figure is not None:
                    nodes.append({"__block__": figure})
                # ponytail: a SmartArt becomes its words, one paragraph per
                # label, in the diagram's data order -- the layout (arrows,
                # cycles) is lost. Render the drawing if a book needs the shape.
                labels = diagram_texts(child, part)
                if labels:
                    nodes.append({"__block__": {"type": "sidebar", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": t}]}
                        for t in labels]}})
                for box in child.iter(f"{W}txbxContent"):
                    content, _ = blocks_of(box, part, footnotes=footnotes or {},
                                           footnote_refs=[], sink=sink)
                    if content:
                        nodes.append({"__block__": {"type": "sidebar", "content": content}})
            elif tag == f"{MC}AlternateContent":
                # Choice is the real content; Fallback is its VML duplicate.
                for choice in child.findall(f"{MC}Choice"):
                    walk_run_children(run, choice, marks)

    def walk(container: etree._Element, extra_marks: list[dict]) -> None:
        for child in container:
            tag = child.tag
            if tag == f"{W}r":
                walk_run_children(child, child, _marks_for(child.find(f"{W}rPr")) + extra_marks)
            elif tag == f"{W}hyperlink":
                href = ""
                rid = child.get(f"{R}id")
                if rid:
                    try:
                        href = part.rels[rid].target_ref
                    except KeyError:
                        href = ""
                link = [{"type": "link", "attrs": {"href": href}}] if href else []
                walk(child, extra_marks + link)
            elif tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    walk(content, extra_marks)
            elif tag in TRANSPARENT:
                walk(child, extra_marks)
            # Anything else -- HIDDEN revisions, pPr, bookmarks, math (refused
            # by source_texts) -- carries no text for the book.

    walk(para, [])
    return nodes, "".join(source)


def _notes_part(document, name: str):
    for part in document.part.package.iter_parts():
        if str(part.partname).endswith(f"/{name}.xml"):
            return part
    return None


def read_footnotes(document, sink: Optional[MediaSink] = None) -> dict[str, list[dict]]:
    """Map note key -> inline content, for footnotes AND endnotes.

    A picture in a note comes back as a `{"__block__": figure}` entry in that
    list, for `paragraph_blocks` to lift out: `footnote.content` is inline-only
    in the schema, and a figure is a block. A real manuscript (a plant pictured
    in the note that names it) refused to ingest at all when notes had no sink.

    Keys are `fn:<id>` and `en:<id>`: the two parts number their notes
    independently, so bare ids collide. An endnote becomes an `endnote` node,
    which the renderers print at the end of its chapter (W9).

    python-docx has no notes API, so each part is located by name and parsed
    directly. A document with no notes has no parts, and yields `{}`.
    """
    notes: dict[str, list[dict]] = {}
    for kind, name, tag in (("fn", "footnotes", "footnote"), ("en", "endnotes", "endnote")):
        part = _notes_part(document, name)
        if part is None:
            continue
        root = etree.fromstring(part.blob)
        for note in root.findall(f"{W}{tag}"):
            if note.get(f"{W}type") in SYNTHETIC_FOOTNOTES:
                continue
            inline: list[dict] = []
            for para in note.findall(f"{W}p"):
                if inline:
                    inline.append({"type": "text", "text": " "})
                # footnote_refs=None: Word does not nest notes.
                inline.extend(_inline_runs(para, part, sink, None)[0])
            inline = _merge_text(inline)
            if any(n.get("text", "").strip() or "__block__" in n for n in inline):
                notes[f"{kind}:{note.get(f'{W}id')}"] = inline
    return notes


def block_children(container: etree._Element):
    """The `w:p` and `w:tbl` of a body, cell or text box, in document order.

    Block-level content controls and custom XML are looked through: a publisher's
    template routinely wraps whole paragraphs in `w:sdt`, and a walk of direct
    children alone skipped every one.
    """
    for child in container:
        if child.tag in (f"{W}p", f"{W}tbl"):
            yield child
        elif child.tag == f"{W}sdt":
            content = child.find(f"{W}sdtContent")
            if content is not None:
                yield from block_children(content)
        elif child.tag == f"{W}customXml":
            yield from block_children(child)


def blocks_of(
    container: etree._Element,
    part,
    *,
    footnotes: dict[str, list[dict]],
    footnote_refs: list,
    sink: Optional[MediaSink],
) -> tuple[list[dict], list[str]]:
    """AST blocks for every paragraph and table in a cell or text box."""
    blocks: list[dict] = []
    sources: list[str] = []
    for el in block_children(container):
        if el.tag == f"{W}tbl":
            node, texts = table_block(el, part, footnotes=footnotes,
                                      footnote_refs=footnote_refs, sink=sink)
            if node is not None:
                blocks.append(node)
        else:
            nodes, texts = paragraph_blocks(el, part, footnotes=footnotes,
                                            footnote_refs=footnote_refs, sink=sink)
            blocks.extend(nodes)
        sources.extend(texts)
    return blocks, sources


def paragraph_blocks(
    para: etree._Element,
    part,
    *,
    footnotes: dict[str, list[dict]],
    footnote_refs: list,
    sink: Optional[MediaSink],
) -> tuple[list[dict], list[str]]:
    """Blocks for one `w:p`, plus the source strings it contained.

    A paragraph holding only a picture yields the figure alone: an empty `<p>`
    around it prints as a blank line above every image.

    ponytail: a `footnote` block lands immediately after its paragraph, so the
    call attaches to the end of that paragraph rather than to the exact word it
    referenced. The AST schema has no inline footnote node (`footnote` is a
    blockNode), and end-of-paragraph is where every renderer here can place a
    call unambiguously. Move to an inline node if mid-sentence calls matter.
    """
    refs: list[tuple[str, int]] = []
    nodes, source_text = _inline_runs(para, part, sink, refs, footnotes)
    footnote_refs.extend(refs)

    lifted = [n["__block__"] for n in nodes if "__block__" in n]
    inline = _merge_text([n for n in nodes if "__block__" not in n])

    blocks: list[dict] = []
    if source_text.strip():
        blocks.append({"type": "paragraph", "content": inline})
    blocks.extend(lifted)
    for key, number in refs:
        note = footnotes.get(key) or []
        content = [n for n in note if "__block__" not in n]
        if any(n.get("text", "").strip() for n in content):
            kind = "footnote" if key.startswith("fn:") else "endnote"
            blocks.append({"type": kind, "attrs": {"number": number}, "content": content})
        # ponytail: a note's pictures print in the text, just after the note,
        # not in the note area -- the schema's footnote holds inline content
        # only. Give `footnote` block content if a book needs them in place.
        blocks.extend(n["__block__"] for n in note if "__block__" in n)
    return blocks, [source_text.strip()] if source_text.strip() else []


def table_block(
    tbl: etree._Element,
    part,
    *,
    footnotes: dict[str, list[dict]],
    footnote_refs: list,
    sink: Optional[MediaSink],
) -> tuple[Optional[dict], list[str]]:
    """A real `table` node, replacing the cell-to-loose-paragraph unroll.

    A table nested in a cell becomes a table in that cell's content.
    """
    rows: list[dict] = []
    sources: list[str] = []

    for tr in tbl.findall(f"{W}tr"):
        cells: list[dict] = []
        for tc in tr.findall(f"{W}tc"):
            content, texts = blocks_of(tc, part, footnotes=footnotes,
                                       footnote_refs=footnote_refs, sink=sink)
            sources.extend(texts)
            if not content:
                # `tableCell.content` has minItems 1, and a blank cell in a
                # spanning row is normal.
                content = [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]
            cell: dict = {"type": "table-cell", "content": content}
            span = tc.find(f"{W}tcPr/{W}gridSpan")
            if span is not None and span.get(f"{W}val", "1") != "1":
                cell["attrs"] = {"colspan": int(span.get(f"{W}val"))}
            cells.append(cell)
        if cells:
            rows.append({"type": "table-row", "content": cells})

    if not rows:
        return None, sources
    # `w:tblHeader` marks a row that repeats across page breaks -- the only
    # header signal a plain manuscript carries.
    first = tbl.find(f"{W}tr")
    if first is not None and first.find(f"{W}trPr/{W}tblHeader") is not None:
        rows[0]["attrs"] = {"header": True}
    return {"type": "table", "content": rows}, sources


def _hidden(el: etree._Element) -> bool:
    return any(a.tag in HIDDEN for a in el.iterancestors())


def _text_roots(document) -> list:
    """(root element, part) of the body and each notes part: the book's text."""
    roots = [(document.element.body, document.part)]
    for name in ("footnotes", "endnotes"):
        part = _notes_part(document, name)
        if part is not None:
            roots.append((etree.fromstring(part.blob), part))
    return roots


def source_texts(document) -> list[str]:
    """Every paragraph of text the document holds, read straight off the XML.

    This is the no-loss oracle, and it deliberately shares nothing with the walk
    above. It used to be that walk's own output, which made the check
    tautological: text inside a container the walk did not know (`w:ins`,
    `w:sdt`, a nested table, a text box) was missing from both sides, so it was
    never missed. Here every `w:t` in the body and the note parts counts,
    whatever it sits inside, unless it is HIDDEN. Headers, footers and comments
    are not book text and are not read.

    Equations are refused, not skipped: OMML text is `m:t`, and no renderer here
    has an equation case, so accepting one would only lose it further down.
    """
    texts: list[str] = []
    for root, part in _text_roots(document):
        for ids in root.iter(f"{DGM}relIds"):
            if not _hidden(ids):
                texts.extend(diagram_texts(ids, part))
        for math in root.iter(f"{M}oMath"):
            if not _hidden(math):
                words = "".join(t.text or "" for t in math.iter(f"{M}t"))
                raise UnsupportedContent(
                    f"the manuscript contains an equation ({words[:40]!r}); "
                    "equations are not supported yet"
                )
        for para in root.iter(f"{W}p"):
            if _hidden(para):
                continue
            chars: list[str] = []
            for el in para.iter(f"{W}t", f"{W}tab", f"{W}noBreakHyphen", f"{W}sym"):
                # Only this paragraph's own text: a text box's paragraphs nest
                # inside it and are counted on their own.
                owner = next(el.iterancestors(f"{W}p"))
                if owner is not para or _hidden(el):
                    continue
                if el.tag == f"{W}t":
                    chars.append(el.text or "")
                elif el.tag == f"{W}noBreakHyphen":
                    chars.append(NO_BREAK_HYPHEN)
                elif el.tag == f"{W}sym":
                    chars.append(sym_char(el))
                elif el.getparent().tag == f"{W}r":  # not a tab stop in w:pPr
                    chars.append(" ")
            text = "".join(chars).strip()
            if text:
                texts.append(text)
    return texts


# Drawing primitives a manuscript can hold that the AST has no node for (W9).
V = "{urn:schemas-microsoft-com:vml}"
WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
VML_SHAPES = tuple(f"{V}{t}" for t in (
    "line", "polyline", "shape", "rect", "oval", "arc", "curve", "roundrect"))


def dropped_drawings(document) -> dict[str, int]:
    """What ingest sees and cannot keep, by kind: `shapes` (lines, arrows and
    boxes with no text), `pictures` (old VML pictures) and `objects` (embedded
    OLE objects). Their TEXT is kept -- a text box's words reach the AST -- so
    this is not text loss and fails nothing; it is reported, so a book whose
    diagrams lost their arrows says so instead of looking complete.

    ponytail: VML pictures are counted, not converted; read `v:imagedata`'s
    r:id into a figure when a book needs them.
    """
    counts = {"shapes": 0, "pictures": 0, "objects": 0}
    for root, _ in _text_roots(document):
        for obj in root.iter(f"{W}object"):
            if not _hidden(obj):
                counts["objects"] += 1
        for el in root.iter(*VML_SHAPES, f"{WPS}wsp"):
            if (_hidden(el) or any(a.tag == f"{W}object" for a in el.iterancestors())
                    or next(el.iter(f"{W}txbxContent"), None) is not None):
                continue
            kind = "pictures" if next(el.iter(f"{V}imagedata"), None) is not None else "shapes"
            counts[kind] += 1
    return counts
