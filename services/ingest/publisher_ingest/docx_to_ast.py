"""DOCX -> `ast/1` conversion.

Why this module has an integrity check of its own
-------------------------------------------------
The pipeline's text-integrity gate (`ast-assemble`) proves that
`normalize(text(html)) == normalize(text(source_ast))` -- i.e. that
*extract* is lossless. It says nothing about whether the AST faithfully
represents the DOCX, because by the time `ast-assemble` runs the DOCX is
long gone. Ingestion is precisely the step that *can* drop text, so it
carries its own post-condition: every non-empty block of the DOCX must
appear in the emitted AST (`_assert_no_text_lost`).

Constraints imposed by `stages.rendering.ast_to_html` (stages/rendering.py)
----------------------------------------------------------------------------
These are not stylistic preferences; violating them fails the build:

* A `chapter` MUST carry `attrs.title`. `ast_to_html` falls back to
  `f"Chapter {number}"` when it is missing, injecting text into the HTML
  side that no source-side text can match.
* frontMatter/backMatter items MUST NOT carry `attrs.title`.
  `ast_to_html` does not render titles for them, but the source-side
  extractor picks up `attrs.title` anywhere it appears -- so a title here
  is counted on one side only.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from .docx_rich import (
    W,
    _toggle_on,
    MediaNotStorable,
    MediaSink,
    UnsupportedContent,
    block_children,
    paragraph_blocks,
    read_footnotes,
    source_texts,
    table_block,
)


class IngestError(RuntimeError):
    """Raised when a DOCX cannot be faithfully represented as an AST."""


# A heading candidate is a short, fully upper-case line. In unstyled
# manuscripts -- the common case, and the one the pipeline's P0 gate targets --
# capitalisation is the only structural signal left; Word styles are absent or,
# worse, actively wrong (a 419-character body paragraph tagged `Heading 1`).
MAX_HEADING_CHARS = 70

# Front matter that appears before the first heading, in document order. A real
# manuscript opens with half title, title page, dedication and epigraphs; we do
# not try to tell them apart, because `extract` renders every front-matter type
# through the same path and the distinction buys nothing downstream.
FRONT_MATTER_TYPE = "titlePage"

# A table of contents is indistinguishable from a run of chapters by heading
# shape alone -- it is *made of* chapter titles, and in an unstyled manuscript
# they are upper-case there too. What separates them is prose: a TOC entry is a
# line, a chapter contains paragraphs. Measured on this corpus, TOC sections cap
# out at ~144 characters in their longest paragraph while the thinnest real
# section runs 540, so the boundary is wide and this threshold sits in the gap.
SUBSTANTIVE_PARAGRAPH_CHARS = 300

# Marks the start of the TOC region. Front matter before it (title page,
# dedication, epigraphs) can legitimately contain long paragraphs -- a block
# epigraph easily clears the prose threshold -- so length alone cannot find the
# body; the search for the first substantive section starts here instead.
#
# Every title pattern here is matched against `_fold(title)`: upper-case with
# accents stripped. Case-insensitive matching alone is not enough for Greek --
# "Περιεχόμενα" carries a tonos, and ό does not fold to Ο -- so a real
# manuscript's contents page, typed in ordinary case, was never found.
TOC_PATTERNS = (
    re.compile(r"ΠΕΡΙΕΧΟΜΕΝΑ"),
    re.compile(r"^(TABLE\s+OF\s+)?CONTENTS$"),
)

# Trailing material that is not part of the book's argument, by the back-matter
# type it becomes.
BACK_MATTER_PATTERNS = (
    (re.compile(r"ΟΠΙΣΘΟΦΥΛΛΟ|ΕΞΩ\s+ΜΕΡΟΣ|^(BACK\s+COVER|COLOPHON)$"), "colophon"),
    (re.compile(r"^(ΒΙΒΛΙΟΓΡΑΦΙΑ|BIBLIOGRAPHY|REFERENCES)$"), "bibliography"),
    (re.compile(r"^(ΕΥΡΕΤΗΡΙΟ|INDEX)$"), "index"),
    (re.compile(r"^(ΠΑΡΑΡΤΗΜΑ|APPENDIX)\b"), "appendix"),
)

# Section names a manuscript sets as a heading with no number and no heading
# style -- in the first real book ingested, bold Normal-style lines. Bold alone
# is far too common to mean "heading" (diagram labels, lead-ins); bold plus one
# of these names is not.
NAMED_SECTION = re.compile(
    r"^(ΠΕΡΙΕΧΟΜΕΝΑ|(TABLE OF )?CONTENTS|ΠΡΟΛΟΓΟΣ|PROLOGUE|PREFACE|FOREWORD|ΕΙΣΑΓΩΓΗ"
    r"|INTRODUCTION|ΕΠΙΛΟΓΟΣ|ΕΠΙΛΟΓΙΚΑ \w+|EPILOGUE|CONCLUSIONS?|ΣΥΜΠΕΡΑΣΜΑΤΑ|ΕΥΧΑΡΙΣΤΙΕΣ"
    r"|ACKNOWLEDG?EMENTS|ΒΙΒΛΙΟΓΡΑΦΙΑ|BIBLIOGRAPHY|REFERENCES|ΕΥΡΕΤΗΡΙΟ|INDEX"
    r"|(ΠΑΡΑΡΤΗΜΑ|APPENDIX)( \S+)?)$"
)

# A section number the author typed: "4." or "4.3.1". Academic manuscripts
# number their headings by hand and set them in bold Normal style -- no heading
# style, not upper-case, so neither older signal fires. Depth 1 is a chapter;
# deeper is a heading inside it. Such titles run long ("4.1 Συσχετίζοντας ...",
# 142 characters), hence a cap of their own.
SECTION_NUMBER = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+\S")
MAX_NUMBERED_HEADING_CHARS = 160

# Confidence that a section is the structural type ingest gave it, on the rules
# engine's scale: below 0.8 escalates for review (publisher_structure.rules,
# LLM_STRATEGY.md §5). Scored where the decision is made, from the evidence that
# made it -- nothing downstream can recover which signal a heading rested on.
#
# ponytail: one number per signal combination. Corroborating evidence (numbering,
# the prose that follows, position in the book) would lift the upper-case-only
# case; add it when review shows unstyled chapters flagged that were never wrong.
STYLED_UPPER_HEADING = 0.95   # a `Heading N` style AND capitalisation agree
STYLED_HEADING = 0.85         # the style alone, on a short line
UPPER_ONLY_HEADING = 0.7      # capitalisation alone -- also true of a shouted "NO!"
UNTITLED_SECTION = 0.5        # prose that no heading introduced, made a chapter anyway
BODY_SPLIT_BY_PROSE = 0.85    # front/body boundary found by the prose threshold
BODY_SPLIT_FALLBACK = 0.5     # nothing cleared that bar; the first heading was taken
BACK_MATTER_BY_PATTERN = 0.9  # the title matched an explicit back-matter string
NUMBERED_BOLD_HEADING = 0.85  # a typed section number AND the whole line bold
NAMED_BOLD_HEADING = 0.85     # a known section name ("Πρόλογος") AND the whole line bold


@dataclass(frozen=True)
class Block:
    """One block-level run of the DOCX, in document order.

    `text` is the plain prose used for heading detection and the no-loss check;
    `nodes` is what actually reaches the AST -- a paragraph with its marks
    intact, plus any figure or footnote the same `w:p` carried.
    """

    text: str
    style: str
    is_heading_candidate: bool
    nodes: tuple[dict, ...] = ()
    heading_confidence: float | None = None


def _fold(text: str) -> str:
    """Upper-case, accents stripped, trailing colon and space dropped."""
    decomposed = unicodedata.normalize("NFD", text)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(bare.upper().split()).rstrip(":").strip()


def _all_bold(p) -> bool:
    """Every run that carries text is bold (direct formatting, as manuscripts set it).

    ponytail: bold inherited from a paragraph or character style is not seen;
    read the style chain if a book sets its headings bold only through a style.
    """
    runs = [r for r in p.iter(f"{W}r") if any((t.text or "").strip() for t in r.iter(f"{W}t"))]
    return bool(runs) and all(_toggle_on(r.find(f"{W}rPr/{W}b")) for r in runs)


def _heading_depth(p, text: str) -> tuple[int | None, float | None]:
    """(depth, confidence) of a bold heading, or (None, None).

    Depth 1 opens a chapter; deeper is a heading inside one.
    """
    if not text or not _all_bold(p):
        return None, None
    number = SECTION_NUMBER.match(text)
    if number and len(text) <= MAX_NUMBERED_HEADING_CHARS:
        return number.group(1).count(".") + 1, NUMBERED_BOLD_HEADING
    if len(text) > MAX_HEADING_CHARS:
        return None, None
    if NAMED_SECTION.match(_fold(text)):
        return 1, NAMED_BOLD_HEADING
    # Not Word list numbering: a bold list item is a list item. The real book's
    # list-numbered "Πρόλογος" is found by name above.
    return None, None


def _is_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _iter_body(document: docx.document.Document) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in true document order.

    `document.paragraphs` skips tables entirely and `document.tables` loses
    position, so neither alone preserves reading order. Neither sees inside a
    block-level content control; `block_children` does.
    """
    for child in block_children(document.element.body):
        if child.tag == f"{W}p":
            yield Paragraph(child, document)
        else:
            yield Table(child, document)


def _is_page_boundary(p) -> bool:
    """A paragraph that ends a page on purpose: a page or section break.

    Kept as a (textless) block because it separates headings. A title page's
    upper-case title and the chapter heading on the next page are otherwise
    adjacent heading candidates, and `_group_headings` joins adjacent headings
    into one display title -- which, in a Word file, made the book's title part
    of chapter one's.
    """
    return (p.find(f"{W}pPr/{W}sectPr") is not None
            or any(br.get(f"{W}type") == "page" for br in p.iter(f"{W}br")))


def read_blocks(
    path: str | Path, *, store_media: MediaSink | None = None
) -> tuple[list[Block], list[str]]:
    """Read a DOCX into ordered blocks, plus every source string it contained.

    Returns `(blocks, source_texts)`. The second list is what
    `_assert_no_text_lost` checks against. It comes from `docx_rich.source_texts`,
    a raw scan of the XML, NOT from the walk that builds `blocks`: fed by the
    walk, the check could only ever confirm the walk agreed with itself.
    """
    document = docx.Document(str(path))
    part = document.part
    # Style names resolved once. `Paragraph.style` re-derives the default style
    # by scanning every style for each unstyled paragraph: 43 s of a 45 s ingest
    # on a 3,900-paragraph book.
    style_names = {s.style_id: s.name for s in document.styles}
    default_style = document.styles.default(WD_STYLE_TYPE.PARAGRAPH)
    default_name = default_style.name if default_style is not None else "Normal"
    footnotes = read_footnotes(document, store_media)
    # Numbering runs across the whole document, so the counter is threaded
    # through every paragraph and cell rather than restarting per block.
    footnote_refs: list = []
    blocks: list[Block] = []
    sources = source_texts(document)

    for item in _iter_body(document):
        if isinstance(item, Table):
            node, texts = table_block(
                item._element, part, footnotes=footnotes,
                footnote_refs=footnote_refs, sink=store_media,
            )
            if node is not None:
                blocks.append(Block(" ".join(texts), "Table", False, (node,)))
            continue

        nodes, texts = paragraph_blocks(
            item._p, part, footnotes=footnotes,
            footnote_refs=footnote_refs, sink=store_media,
        )
        style_ref = item._p.find(f"{W}pPr/{W}pStyle")
        style = style_names.get(style_ref.get(f"{W}val") if style_ref is not None else None,
                                default_name)
        if not nodes:
            if _is_page_boundary(item._p):
                blocks.append(Block("", style, False))
            continue

        text = texts[0] if texts else ""
        # `Heading N` is trusted only when it is also short -- see the
        # 419-character `Heading 1` paragraph noted in the module docstring.
        # A Word TOC entry (`toc 1`...) is a chapter title, upper-case in an
        # unstyled book, and is never itself a heading.
        short = len(text) <= MAX_HEADING_CHARS
        upper = short and _is_upper(text)
        styled = style.startswith("Heading")
        toc_entry = style.lower().startswith("toc ")
        is_heading = bool(text) and short and (upper or styled) and not toc_entry
        confidence = None
        if is_heading:
            confidence = (STYLED_UPPER_HEADING if upper and styled
                          else STYLED_HEADING if styled else UPPER_ONLY_HEADING)
        elif not toc_entry:
            depth, bold_confidence = _heading_depth(item._p, text)
            if depth == 1:
                is_heading, confidence = True, bold_confidence
            elif depth and nodes[0].get("type") == "paragraph":
                # A section inside a chapter: kept in place, as a heading node.
                nodes = [{"type": "heading", "attrs": {"level": min(depth, 6)},
                          "content": nodes[0]["content"]}, *nodes[1:]]
        blocks.append(Block(text, style, is_heading, tuple(nodes), confidence))

    return blocks, sources


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _node_text(node) -> str:
    """All prose under an AST node, for length-based structural heuristics."""
    if isinstance(node, list):
        return "".join(_node_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    return _node_text(node.get("content") or [])


def _group_headings(
    blocks: list[Block],
) -> list[tuple[str, list[dict], float | None, list[dict]]]:
    """Split blocks into (title, body_paragraphs, title_confidence, title_lines) sections.

    `title_lines` are the title's own paragraph nodes, one per source line, with
    their marks. A section that is NOT a chapter -- front matter, or anything in
    back matter -- has no title field, so its title goes back into its content;
    it goes back as these lines, not as the joined title string. Joined, a title
    page's three display lines became one paragraph, and two adjacent
    bibliography entries an author had styled `Heading 1` became one citation.

    Consecutive heading candidates collapse into a single title: a display
    title set over three lines ("Β΄ ΕΝΟΤΗΤΑ" / "ΜΗΧΑΝΙΚΗ ΨΥΧΗ" / "ΚΑΙ" /
    "ΕΥΧΑΡΙΣΤΙΑΚΟ ΠΟΤΗΡΙΟ") is one heading, not four empty chapters -- and is
    only as certain as its least certain line.

    The leading run before the first heading is returned with an empty title
    and no confidence: no heading decision was made for it.
    """
    sections: list[tuple[str, list[dict], float | None, list[dict]]] = []
    title_parts: list[Block] = []
    body: list[dict] = []
    current_title = ""
    current_confidence: float | None = None
    current_lines: list[dict] = []
    seen_heading = False

    def lines_of(parts: list[Block]) -> list[dict]:
        return [n for b in parts for n in b.nodes if n.get("type") == "paragraph"]

    def take_title() -> None:
        nonlocal current_title, current_confidence, current_lines
        current_title = " ".join(b.text for b in title_parts)
        scores = [b.heading_confidence for b in title_parts if b.heading_confidence is not None]
        current_confidence = min(scores) if scores else None
        current_lines = lines_of(title_parts)
        title_parts.clear()

    def close() -> None:
        nonlocal current_title, current_confidence, current_lines, body
        if current_title or body:
            sections.append((current_title, body, current_confidence, current_lines))
        current_title = ""
        current_confidence = None
        current_lines = []
        body = []

    for block in blocks:
        if block.is_heading_candidate:
            if _is_toc_marker(block.text):
                # The contents page is a hard section break. Without it the
                # marker joins the run of chapter titles printed beneath it --
                # upper-case in an unstyled manuscript, so heading candidates
                # too -- and the whole table of contents collapses into chapter
                # one's heading.
                if title_parts:
                    take_title()
                close()
                current_title = block.text
                current_confidence = block.heading_confidence
                current_lines = lines_of([block])
                seen_heading = True
                continue
            if not title_parts:
                # Starting a new heading: close out the previous section.
                close()
            title_parts.append(block)
            # A heading contributes its text as the section title, so its
            # paragraph node is redundant -- but a footnote or figure hanging
            # off that same `w:p` is not, and dropping it is exactly the silent
            # loss this extractor exists to end. Those open the new section.
            body.extend(n for n in block.nodes if n.get("type") != "paragraph")
            seen_heading = True
            continue

        if title_parts:
            take_title()
        elif not seen_heading:
            current_title = ""
        body.extend(block.nodes)

    if title_parts:
        take_title()
    close()

    return sections


def _back_matter_type(title: str) -> str | None:
    folded = _fold(title)
    return next((kind for pattern, kind in BACK_MATTER_PATTERNS if pattern.search(folded)), None)


def _is_toc_marker(title: str) -> bool:
    folded = _fold(title)
    return any(p.search(folded) for p in TOC_PATTERNS)


def _is_substantive(nodes: list[dict]) -> bool:
    return any(len(_node_text(n)) >= SUBSTANTIVE_PARAGRAPH_CHARS for n in nodes)


def _find_body_start(sections: list[tuple]) -> int:
    """Index of the first section that belongs to the book's body.

    Everything before it -- title page, dedication, epigraphs, table of
    contents -- is front matter. The scan for real prose begins at the TOC
    marker when there is one, so that a long epigraph printed ahead of the
    contents page is not mistaken for chapter one.
    """
    start = 0
    for i, (title, *_) in enumerate(sections):
        if _is_toc_marker(title):
            start = i
            break

    for i in range(start, len(sections)):
        if _is_substantive(sections[i][1]):
            return i

    # No section anywhere clears the prose bar. Treat the first heading as the
    # body rather than emitting a book with no chapters at all.
    return start


# Node types ast.schema.json lets carry a `sourceRef`: chapters and block nodes.
# Not the front/back-matter section wrappers (the schema has no field for it),
# table rows/cells, or inline runs.
SOURCE_REF_TYPES = frozenset({
    "part", "chapter", "paragraph", "heading", "blockquote", "verse", "list", "table",
    "figure", "footnote", "epigraph", "sceneBreak", "dialogue", "sidebar", "code",
    "equation", "pageBreak",
})


def _assign_source_refs(ast: dict) -> None:
    """Give every addressable node a `sourceRef.docxId` an override can target.

    Overrides find their node by this id (publisher_structure.overrides), and
    ingest used to emit none: every override matched nothing and was skipped.

    The id is derived from CONTENT, not position. A positional id ("p412") shifts
    for every node after an insertion, so a stored op would silently retarget a
    DIFFERENT paragraph -- worse than not matching at all. A content id survives
    any edit that does not touch its own node, and when it does, the op orphans
    rather than landing somewhere wrong. Chapters are keyed by title, not body,
    so editing a paragraph does not orphan a retitle of its chapter. Identical
    content (scene breaks, a repeated line) is told apart by occurrence order.

    `docxId` is the schema's field name; the value is not an id Word assigned.
    ponytail: `w14:paraId` is Word's own paragraph id, but Word regenerates it on
    some edits and other writers omit it, so content is the more stable key. Use
    paraId to break ties if occurrence-order ids prove fragile in practice.
    """
    seen: dict[str, int] = {}

    def identity(node: dict) -> str:
        if node["type"] in ("chapter", "part"):
            basis = (node.get("attrs") or {}).get("title") or ""
        else:
            # A node with no prose (figure, scene break) is identified by what it
            # IS: a figure's attrs carry its media hash.
            basis = _node_text(node) or json.dumps(node, sort_keys=True, ensure_ascii=False)
        return f"{node['type']}:{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"

    def walk(nodes) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            if node.get("type") in SOURCE_REF_TYPES:
                base = identity(node)
                seen[base] = seen.get(base, 0) + 1
                node["sourceRef"] = {"docxId": base if seen[base] == 1 else f"{base}~{seen[base]}"}
            walk(node.get("content"))

    for root in ("frontMatter", "body", "backMatter"):
        walk(ast.get(root))


def _assert_no_text_lost(sources: list[str], ast: dict) -> None:
    """Post-condition: no DOCX text was dropped on the way into the AST.

    Compared on whitespace-stripped text, since the AST stores block text
    verbatim and only the pipeline's own normalizer may collapse runs.
    """
    emitted: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            title = (node.get("attrs") or {}).get("title")
            if title:
                emitted.append(title)
            if isinstance(node.get("content"), list):
                # One chunk per block, with its text nodes concatenated and NOT
                # separated: marks split a single sentence into several text
                # nodes ("Plain and ", "italic", " and "), and a separator
                # between them destroys the contiguity this check needs. Nesting
                # means a block's text is also counted inside its parent's
                # chunk; harmless, since this is a substring test.
                emitted.append(_node_text(node["content"]))
            for key in ("frontMatter", "body", "backMatter", "content"):
                walk(node.get(key))
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(ast)
    haystack = "\n".join(emitted)
    # Most sources are exactly one emitted block: a set lookup first, and the
    # substring scan (quadratic over a whole book) only for the rest.
    whole = {e.strip() for e in emitted}

    missing = [s for s in sources if s not in whole and s not in haystack]
    if missing:
        raise IngestError(
            f"{len(missing)} of {len(sources)} DOCX text blocks did not reach the "
            f"AST. First dropped block: {missing[0][:120]!r}"
        )


def docx_to_ast(
    path: str | Path,
    *,
    title: str | None = None,
    language: str = "el-GR",
    manuscript_id: str | None = None,
    store_media: MediaSink | None = None,
) -> dict:
    """Convert a DOCX manuscript into an `ast/1` document.

    `store_media` receives `(bytes, media_type, original_name)` for each
    embedded image and returns its sha256; the AST then references the image by
    that hash. Omit it only for manuscripts known to have no images -- a
    picture met with no sink raises rather than being silently dropped.

    Raises `IngestError` if any DOCX text would be lost.
    """
    source = Path(path)
    if not source.exists():
        raise IngestError(f"DOCX not found: {source}")

    try:
        blocks, sources = read_blocks(source, store_media=store_media)
    except (MediaNotStorable, UnsupportedContent) as e:
        raise IngestError(str(e)) from e
    except etree.XMLSyntaxError as e:
        # E3.4 audit: docx_rich.read_footnotes() parses word/footnotes.xml
        # directly (python-docx has no footnote API), via lxml's default
        # etree.fromstring() -- unlike document.xml, which python-docx's own
        # Document() constructor parses. A malformed or hostile footnotes.xml
        # (e.g. a billion-laughs payload; verified empirically that lxml's
        # built-in entity-amplification guard already refuses it, raising
        # exactly this exception) would otherwise leak past this function as
        # an unclassified lxml error instead of the BAD_INPUT this module
        # exists to produce for every other malformed-DOCX case.
        raise IngestError(f"malformed XML in a DOCX part: {e}") from e
    if not blocks:
        raise IngestError(f"DOCX contains no text: {source}")

    sections = _group_headings(blocks)
    body_start = _find_body_start(sections)
    # Front matter's confidence is in its PLACEMENT (before the body, not chapter
    # one), which is only as good as the boundary. Its subtype is deliberately
    # not scored: FRONT_MATTER_TYPE lumps title page, dedication and epigraphs
    # on purpose, and flagging that lump would bury every real question.
    split_found = body_start < len(sections) and _is_substantive(sections[body_start][1])
    front_confidence = BODY_SPLIT_BY_PROSE if split_found else BODY_SPLIT_FALLBACK

    front_matter: list[dict] = []
    body: list[dict] = []
    back_matter: list[dict] = []
    chapter_number = 0
    seen_toc = False

    for index, (section_title, section_nodes, title_confidence, title_lines) in enumerate(sections):
        content = list(section_nodes)
        # A non-chapter's title returns to its content line by line (see
        # `_group_headings`); the joined string only if no line node survived.
        title_paragraphs = title_lines or ([_paragraph(section_title)] if section_title else [])

        if index < body_start:
            # Front matter carries no `attrs.title` -- see module docstring --
            # so its heading survives as a leading paragraph instead.
            if _is_toc_marker(section_title):
                seen_toc = True
            content[0:0] = title_paragraphs
            front_matter.append(
                {
                    "type": "toc" if seen_toc else FRONT_MATTER_TYPE,
                    "content": content,
                    "confidence": front_confidence,
                }
            )
            continue

        kind = _back_matter_type(section_title)
        if kind:
            back_matter.append(
                {
                    "type": kind,
                    "content": [*title_paragraphs, *content],
                    "confidence": BACK_MATTER_BY_PATTERN,
                }
            )
            continue
        if back_matter:
            # Nothing after back matter is a chapter. A heading here is one the
            # back matter itself contains -- in the first real book, bibliography
            # entries its author had styled `Heading 1`/`Heading 3` -- so it stays
            # inside that section as the paragraph it is, rather than opening a
            # chapter titled with a citation.
            back_matter[-1]["content"].extend([*title_paragraphs, *content])
            continue

        chapter_number += 1
        chapter = {
            "type": "chapter",
            "attrs": {
                "number": chapter_number,
                "title": section_title,
                "id": f"ch{chapter_number}",
                "startsOn": "recto",
            },
            "content": content,
        }
        # Absent, never defaulted: a heading block with no score was not measured.
        confidence = title_confidence if section_title else UNTITLED_SECTION
        if confidence is not None:
            chapter["confidence"] = confidence
        body.append(chapter)

    if not body:
        raise IngestError(
            f"No chapters detected in {source.name}. Every block landed in front "
            "matter, which means no heading was recognised."
        )

    # A document of nothing but headings yields chapters with no paragraphs --
    # a "book" whose every page is a title. That is a misread document, not a
    # thin one, and it must not travel three stages downstream to surface as an
    # empty render.
    if not any(chapter["content"] for chapter in body):
        raise IngestError(
            f"No prose found in {source.name}: every block was read as a heading. "
            "Check that the document actually contains body text."
        )

    ast = {
        "schema": "ast/1",
        "metadata": {
            "title": title or source.stem,
            "language": language,
        },
        "frontMatter": front_matter,
        "body": body,
        "backMatter": back_matter,
        "integrityHash": "",
        "sourceRef": {
            "manuscriptId": manuscript_id or source.stem,
            "inferenceVersion": 1,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }

    _assign_source_refs(ast)
    _assert_no_text_lost(sources, ast)

    all_text = " ".join(b.text for b in blocks)
    ast["integrityHash"] = "sha256:" + hashlib.sha256(
        all_text.encode("utf-8")
    ).hexdigest()

    return ast
