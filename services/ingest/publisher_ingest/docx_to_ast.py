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

Constraints imposed by `extract._ast_to_html` (stages/extract_stage.py)
----------------------------------------------------------------------
These are not stylistic preferences; violating them fails the build:

* A `chapter` MUST carry `attrs.title`. `_ast_to_html` falls back to
  `f"Chapter {number}"` when it is missing, injecting text into the HTML
  side that no source-side text can match.
* frontMatter/backMatter items MUST NOT carry `attrs.title`.
  `_ast_to_html` does not render titles for them, but the source-side
  extractor picks up `attrs.title` anywhere it appears -- so a title here
  is counted on one side only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from .docx_rich import (
    MediaNotStorable,
    MediaSink,
    paragraph_blocks,
    read_footnotes,
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
TOC_PATTERNS = (
    re.compile(r"ΠΕΡΙΕΧΟΜΕΝΑ", re.IGNORECASE),
    re.compile(r"^\s*(TABLE\s+OF\s+)?CONTENTS\s*$", re.IGNORECASE),
)

# Trailing material that is not part of the book's argument. Matched against a
# heading title, case-insensitively.
BACK_MATTER_PATTERNS = (
    re.compile(r"ΟΠΙΣΘΟΦΥΛΛΟ|ΕΞΩ\s+ΜΕΡΟΣ", re.IGNORECASE),
    re.compile(r"^\s*(BACK\s+COVER|COLOPHON)\s*$", re.IGNORECASE),
)


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


def _is_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _iter_body(document: docx.document.Document) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in true document order.

    `document.paragraphs` skips tables entirely and `document.tables` loses
    position, so neither alone preserves reading order.
    """
    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def read_blocks(
    path: str | Path, *, store_media: MediaSink | None = None
) -> tuple[list[Block], list[str]]:
    """Read a DOCX into ordered blocks, plus every source string it contained.

    Returns `(blocks, source_texts)`. The second list is what
    `_assert_no_text_lost` checks against, and it is collected here rather than
    derived from `blocks` because a table contributes one block but many
    strings -- one per cell paragraph.
    """
    document = docx.Document(str(path))
    part = document.part
    footnotes = read_footnotes(document)
    # Numbering runs across the whole document, so the counter is threaded
    # through every paragraph and cell rather than restarting per block.
    footnote_refs: list = []
    blocks: list[Block] = []
    sources: list[str] = []

    for item in _iter_body(document):
        if isinstance(item, Table):
            node, texts = table_block(
                item._element, part, footnotes=footnotes,
                footnote_refs=footnote_refs, sink=store_media,
            )
            sources.extend(texts)
            if node is not None:
                blocks.append(Block(" ".join(texts), "Table", False, (node,)))
            continue

        nodes, texts = paragraph_blocks(
            item._p, part, footnotes=footnotes,
            footnote_refs=footnote_refs, sink=store_media,
        )
        if not nodes:
            continue
        sources.extend(texts)

        text = texts[0] if texts else ""
        style = item.style.name if item.style is not None else "Normal"
        # `Heading N` is trusted only when it is also short -- see the
        # 419-character `Heading 1` paragraph noted in the module docstring.
        short = len(text) <= MAX_HEADING_CHARS
        is_heading = bool(text) and short and (_is_upper(text) or style.startswith("Heading"))
        blocks.append(Block(text, style, is_heading, tuple(nodes)))

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


def _group_headings(blocks: list[Block]) -> list[tuple[str, list[dict]]]:
    """Split blocks into (title, body_paragraphs) sections.

    Consecutive heading candidates collapse into a single title: a display
    title set over three lines ("Β΄ ΕΝΟΤΗΤΑ" / "ΜΗΧΑΝΙΚΗ ΨΥΧΗ" / "ΚΑΙ" /
    "ΕΥΧΑΡΙΣΤΙΑΚΟ ΠΟΤΗΡΙΟ") is one heading, not four empty chapters.

    The leading run before the first heading is returned with an empty title.
    """
    sections: list[tuple[str, list[dict]]] = []
    title_parts: list[str] = []
    body: list[dict] = []
    current_title = ""
    seen_heading = False

    def close() -> None:
        nonlocal current_title, body
        if current_title or body:
            sections.append((current_title, body))
        current_title = ""
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
                    current_title = " ".join(title_parts)
                    title_parts.clear()
                close()
                current_title = block.text
                seen_heading = True
                continue
            if not title_parts:
                # Starting a new heading: close out the previous section.
                close()
            title_parts.append(block.text)
            # A heading contributes its text as the section title, so its
            # paragraph node is redundant -- but a footnote or figure hanging
            # off that same `w:p` is not, and dropping it is exactly the silent
            # loss this extractor exists to end. Those open the new section.
            body.extend(n for n in block.nodes if n.get("type") != "paragraph")
            seen_heading = True
            continue

        if title_parts:
            current_title = " ".join(title_parts)
            title_parts.clear()
        elif not seen_heading:
            current_title = ""
        body.extend(block.nodes)

    if title_parts:
        current_title = " ".join(title_parts)
        title_parts.clear()
    close()

    return sections


def _is_back_matter(title: str) -> bool:
    return any(p.search(title) for p in BACK_MATTER_PATTERNS)


def _is_toc_marker(title: str) -> bool:
    return any(p.search(title) for p in TOC_PATTERNS)


def _find_body_start(sections: list[tuple[str, list[dict]]]) -> int:
    """Index of the first section that belongs to the book's body.

    Everything before it -- title page, dedication, epigraphs, table of
    contents -- is front matter. The scan for real prose begins at the TOC
    marker when there is one, so that a long epigraph printed ahead of the
    contents page is not mistaken for chapter one.
    """
    start = 0
    for i, (title, _) in enumerate(sections):
        if _is_toc_marker(title):
            start = i
            break

    for i in range(start, len(sections)):
        _, nodes = sections[i]
        if any(len(_node_text(n)) >= SUBSTANTIVE_PARAGRAPH_CHARS for n in nodes):
            return i

    # No section anywhere clears the prose bar. Treat the first heading as the
    # body rather than emitting a book with no chapters at all.
    return start


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

    missing = [s for s in sources if s not in haystack]
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
    except MediaNotStorable as e:
        raise IngestError(str(e)) from e
    if not blocks:
        raise IngestError(f"DOCX contains no text: {source}")

    sections = _group_headings(blocks)
    body_start = _find_body_start(sections)

    front_matter: list[dict] = []
    body: list[dict] = []
    back_matter: list[dict] = []
    chapter_number = 0
    seen_toc = False

    for index, (section_title, section_nodes) in enumerate(sections):
        content = list(section_nodes)

        if index < body_start:
            # Front matter carries no `attrs.title` -- see module docstring --
            # so its heading survives as a leading paragraph instead.
            if _is_toc_marker(section_title):
                seen_toc = True
            if section_title:
                content.insert(0, _paragraph(section_title))
            front_matter.append(
                {
                    "type": "toc" if seen_toc else FRONT_MATTER_TYPE,
                    "content": content,
                }
            )
            continue

        if _is_back_matter(section_title):
            back_matter.append(
                {"type": "colophon", "content": [_paragraph(section_title), *content]}
            )
            continue

        chapter_number += 1
        body.append(
            {
                "type": "chapter",
                "attrs": {
                    "number": chapter_number,
                    "title": section_title,
                    "id": f"ch{chapter_number}",
                    "startsOn": "recto",
                },
                "content": content,
            }
        )

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

    _assert_no_text_lost(sources, ast)

    all_text = " ".join(b.text for b in blocks)
    ast["integrityHash"] = "sha256:" + hashlib.sha256(
        all_text.encode("utf-8")
    ).hexdigest()

    return ast
