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
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


class IngestError(RuntimeError):
    """Raised when a DOCX cannot be faithfully represented as an AST."""


def _fold_diacritics(text: str) -> str:
    """Casefold and strip combining marks, so "Περιεχόμενα" reaches ΠΕΡΙΕΧΟΜΕΝΑ.

    Applied to BOTH the marker patterns and the line being tested. Greek marks
    the tonos on the stressed vowel, and `re.IGNORECASE` does not relate "ό" to
    the "Ο" in ΠΕΡΙΕΧΟΜΕΝΑ -- case-folding maps ό to Ό, not to ο. So the
    contents marker never matched on a real Greek manuscript: no `toc`
    front-matter item was ever emitted, and the contents page (with every
    chapter title printed beneath it) was swallowed into chapter one. Folding
    first is what makes TOC_PATTERNS mean what they were written to mean.
    """
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", stripped).casefold()


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
    re.compile(_fold_diacritics("ΠΕΡΙΕΧΟΜΕΝΑ"), re.IGNORECASE),
    re.compile(r"^\s*(TABLE\s+OF\s+)?CONTENTS\s*$", re.IGNORECASE),
)

# A numbered section heading: "4.3.1 Κοινότητα στοιχείων ...". The leading
# backtick/tab alternative absorbs the stray "`\t" prefix Word leaves on entries
# promoted from a list.
#
# WHY THIS OUTRANKS THE Heading STYLES
# The module docstring warns that Word styles can be "actively wrong"; on the
# manuscript this was built against, every single `Heading 1`/`Heading 3` in the
# file sits on a BIBLIOGRAPHY ENTRY and not one real section carries a heading
# style. Trusting the styles produced chapters titled "Γκοραΐνωφ, Ε. (2018).
# Άγιος Σεραφείμ του Σάρωφ." while the actual book -- 14 numbered sections with
# their own numbered subsections -- collapsed into a single 2,239-paragraph
# chapter. When a document numbers its sections, the numbering IS the structure;
# it is authorial, explicit, and hierarchical, which no style guess is.
NUMBERED_HEADING = re.compile(
    r"^[`\s\u00a0]*(\d+(?:\.\d+)*)\.?[\s\u00a0]+(\S.*)$"
)

# A numbered heading may legitimately run long -- academic section titles do --
# so the short-line rule that guards the upper-case heuristic is relaxed here.
# The cap only exists to stop a body paragraph that happens to open with "1. "
# from being read as a section.
MAX_NUMBERED_HEADING_CHARS = 220

# Below this many numbered lines, the document is not "a numbered document" and
# the legacy upper-case/style heuristics stay in charge. A handful of numbered
# lines is a list; forty of them, at consistent depths, is a table of contents
# and a body that agree with each other.
MIN_NUMBERED_HEADINGS = 8

# Trailing material that is not part of the book's argument. Matched against a
# heading title, case-insensitively.
BACK_MATTER_PATTERNS = (
    re.compile(
        _fold_diacritics("ΟΠΙΣΘΟΦΥΛΛΟ") + r"|" + _fold_diacritics("ΕΞΩ") + r"\s+" + _fold_diacritics("ΜΕΡΟΣ"),
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(BACK\s+COVER|COLOPHON)\s*$", re.IGNORECASE),
)


@dataclass(frozen=True)
class Block:
    """One block-level run of text from the DOCX, in document order."""

    text: str
    style: str
    is_heading_candidate: bool
    # Set only in numbered mode: 1 for a top-level section ("5 ..."), 2 for
    # "5.1 ...", and so on. 0 means "not a numbered heading".
    depth: int = 0
    # (character offset into `text`, footnote id) for each reference Word
    # placed in this block, in document order. The offset is what lets the call
    # be set where the author put it instead of at the end of the paragraph.
    footnote_refs: tuple[tuple[int, str], ...] = ()


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


# Word reserves footnote ids 0 and -1 for the separator rules it draws above the
# footnote area. They carry a `w:type` and no authorial text; emitting them would
# put a stray empty note on the page.
_FOOTNOTE_SEPARATOR_TYPES = {"separator", "continuationSeparator", "continuationNotice"}


def read_footnotes(path: str | Path) -> dict[str, str]:
    """Map footnote id -> its text, read straight from `word/footnotes.xml`.

    python-docx models the document body only: `Document.paragraphs` never
    reaches the footnote part, so a manuscript's notes are invisible to
    `_iter_body` and were silently dropped on the way into the AST. The
    text-integrity gate could not catch it either -- `ast-assemble` compares the
    AST against the HTML *derived from that AST*, and `_assert_no_text_lost`
    compares against the blocks this module read. Text no reader ever saw is
    absent from both sides of both comparisons, so it vanishes with every gate
    still reporting green. That is exactly why this function exists and why its
    output is fed to `_assert_no_text_lost` below.
    """
    try:
        with zipfile.ZipFile(str(path)) as zf:
            if "word/footnotes.xml" not in zf.namelist():
                return {}
            raw = zf.read("word/footnotes.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise IngestError(f"could not read footnotes from {path}: {exc}") from exc

    # `docx.oxml.parse_xml` carries the same hardened parser the rest of
    # python-docx uses (no entity resolution) -- see the XXE test in
    # services/ingest/tests.
    from docx.oxml import parse_xml

    root = parse_xml(raw)
    notes: dict[str, str] = {}
    for note in root.findall(qn("w:footnote")):
        note_id = note.get(qn("w:id"))
        if note_id is None or note.get(qn("w:type")) in _FOOTNOTE_SEPARATOR_TYPES:
            continue
        text = "".join(t.text or "" for t in note.iter(qn("w:t"))).strip()
        if text:
            notes[note_id] = text
    return notes


def _footnote_refs_in(paragraph: Paragraph) -> tuple[tuple[int, str], ...]:
    """(offset, footnote id) for each reference in this paragraph.

    The offset counts characters of `w:t` text seen so far, so it indexes into
    exactly the string python-docx returns as `paragraph.text` -- the same
    string that becomes `Block.text`. Walking the XML in document order is what
    makes the two agree: `paragraph.text` is itself the concatenation of those
    `w:t` nodes in that order.
    """
    refs: list[tuple[int, str]] = []
    offset = 0
    for node in paragraph._p.iter():
        tag = node.tag.split("}")[-1]
        if tag == "t":
            offset += len(node.text or "")
        elif tag == "footnoteReference":
            ref_id = node.get(qn("w:id"))
            if ref_id is not None:
                refs.append((offset, ref_id))
    return tuple(refs)


def read_blocks(path: str | Path) -> list[Block]:
    """Flatten a DOCX into ordered, non-empty text blocks."""
    document = docx.Document(str(path))
    blocks: list[Block] = []

    for item in _iter_body(document):
        if isinstance(item, Table):
            # Table text still has to survive; the pipeline has no table-aware
            # layout yet, so cells degrade to paragraphs rather than vanish.
            for row in item.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        text = para.text.strip()
                        if text:
                            blocks.append(Block(text, "table cell", False))
            continue

        text = item.text.strip()
        if not text:
            continue

        style = item.style.name if item.style is not None else "Normal"
        # `Heading N` is trusted only when it is also short -- see the
        # 419-character `Heading 1` paragraph noted in the module docstring.
        short = len(text) <= MAX_HEADING_CHARS
        is_heading = short and (_is_upper(text) or style.startswith("Heading"))
        blocks.append(
            Block(text, style, is_heading, footnote_refs=_footnote_refs_in(item))
        )

    return _apply_numbering(blocks)


def _numbered_depth(text: str) -> int:
    """Depth of a numbered heading line, or 0 when the line is not one."""
    if len(text) > MAX_NUMBERED_HEADING_CHARS:
        return 0
    match = NUMBERED_HEADING.match(text)
    if not match:
        return 0
    return len(match.group(1).split("."))


def _toc_outline(blocks: list[Block]) -> tuple[int, int, dict[str, int]] | None:
    """`(toc_index, body_start_index, {folded title: depth})` from the contents page.

    WHY THE CONTENTS PAGE IS THE AUTHORITY
    On the manuscript this was built against, the numbering lives ONLY on the
    contents page: the TOC reads "4.3.1 Κοινότητα στοιχείων ...", while the
    section it points at is typed in the body as the bare line "Κοινότητα
    στοιχείων ...". Detecting headings by their numbering therefore finds every
    section in the wrong place -- it matches the contents entries themselves, and
    then, in the body, only the numbered LISTS inside the prose ("3. Μεταξύ του
    δευτέρου και του τρίτου ταξιδιού του Πλάτωνος ..."), which are not sections
    at all. Reading the outline off the contents page and then matching those
    titles against the body gets both right, and gets the depth for free: the
    author already declared the hierarchy by numbering it.
    """
    marker = next((i for i, b in enumerate(blocks) if _is_toc_marker(b.text)), None)
    if marker is None:
        return None

    prose = next(
        (
            i
            for i in range(marker + 1, len(blocks))
            if len(blocks[i].text) >= SUBSTANTIVE_PARAGRAPH_CHARS
        ),
        None,
    )
    if prose is None:
        return None

    entries: dict[str, int] = {}
    for block in blocks[marker + 1 : prose]:
        depth = _numbered_depth(block.text)
        if depth:
            match = NUMBERED_HEADING.match(block.text)
            title = match.group(2) if match else block.text
        else:
            # An unnumbered contents line ("Βιβλιογραφία") is still a section.
            title, depth = block.text, 1
        folded = _fold_diacritics(title.strip())
        if folded:
            entries.setdefault(folded, depth)

    if len(entries) < MIN_NUMBERED_HEADINGS:
        return None

    # The body's first heading is the short line immediately before its first
    # paragraph of prose -- and, being a section title, it is one of the entries
    # above. Without this the contents page appears to run one line too long and
    # the book's opening section is read as the last contents entry.
    body_start = prose
    if prose - 1 > marker and _fold_diacritics(blocks[prose - 1].text) in entries:
        body_start = prose - 1

    return marker, body_start, entries


def _apply_numbering(blocks: list[Block]) -> list[Block]:
    """Promote section headings, preferring the contents page's own outline.

    Three tiers, most authoritative first:
      1. the contents page (`_toc_outline`) -- the author's declared hierarchy;
      2. numbering in the body, when the document numbers its sections there;
      3. neither, in which case the legacy upper-case/style heuristics keep the
         blocks unchanged -- what every non-numbered manuscript, and every
         fixture in services/ingest/tests, relies on.
    """
    outline = _toc_outline(blocks)
    if outline is not None:
        marker, body_start, entries = outline
        promoted: list[Block] = []
        for i, block in enumerate(blocks):
            depth = 0
            if i == marker:
                depth = 1                      # opens the contents section
            elif i >= body_start and len(block.text) <= MAX_NUMBERED_HEADING_CHARS:
                # Matched with AND without a leading number: this manuscript is
                # inconsistent about it -- section 1 is typed "Πρόλογος" while
                # section 2 is typed "2. Εισαγωγή" -- and both name the same
                # contents entry. Trying the bare title second is also what keeps
                # a numbered LIST inside the prose from matching: "3. Μεταξύ του
                # δευτέρου ..." strips to a title the contents page never names.
                match = NUMBERED_HEADING.match(block.text)
                for candidate in (
                    _fold_diacritics(block.text),
                    _fold_diacritics(match.group(2).strip()) if match else "",
                ):
                    if candidate and candidate in entries:
                        depth = entries[candidate]
                        break
            # Between the marker and body_start lie the contents entries
            # themselves: left as plain blocks so they stay inside the contents
            # section and are emitted as one `toc` item.
            promoted.append(
                Block(block.text, block.style, bool(depth), depth, block.footnote_refs)
            )
        return promoted

    depths = [_numbered_depth(b.text) for b in blocks]
    if sum(1 for d in depths if d) < MIN_NUMBERED_HEADINGS:
        return blocks

    promoted = []
    for block, depth in zip(blocks, depths):
        if not depth and _is_toc_marker(block.text):
            depth = 1
        promoted.append(
            Block(block.text, block.style, bool(depth), depth, block.footnote_refs)
        )
    return promoted


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


# depth -> (heading level, role). Depth 1 is a chapter and never reaches here.
_HEADING_ROLE = {2: "section", 3: "subsection"}


def _heading(text: str, depth: int) -> dict:
    return {
        "type": "heading",
        "attrs": {
            "level": min(depth, 6),
            "role": _HEADING_ROLE.get(depth, "subsubsection"),
        },
        "content": [{"type": "text", "text": text}],
    }


def _footnote(text: str, number: int) -> dict:
    return {
        "type": "footnote",
        "attrs": {"number": number},
        "content": [{"type": "text", "text": text}],
    }


def _inline_with_footnotes(
    block: Block, footnotes: dict[str, str], counter: list[int]
) -> list[dict]:
    """Inline content for one block, with each footnote AT ITS REFERENCE POINT.

    `footnote` is both a blockNode and an inlineNode in ast/1. Inline is what a
    footnote reference actually is -- it happens at a point inside a sentence --
    and it is what puts the call where the author put it: WeasyPrint generates
    `::footnote-call` wherever the element sits, so a note emitted after the
    paragraph produced a call hanging off the paragraph's last word, several
    lines from the sentence it belonged to.

    The text is cut at the offsets `_footnote_refs_in` recorded and the notes
    are spliced into the gaps. Cutting rather than splitting into separate
    paragraphs matters: the paragraph stays one paragraph, so no boundary the
    manuscript does not have is invented, and `ast-assemble` still sees the same
    text stream on both sides because `_render_inline` walks this list in order.
    """
    text = block.text
    refs = [(o, i) for o, i in block.footnote_refs if footnotes.get(i)]
    if not refs:
        return [{"type": "text", "text": text}]

    content: list[dict] = []
    cursor = 0
    for offset, note_id in refs:
        # Word can record an offset past the stripped text (a reference sitting
        # in trailing whitespace); clamp rather than slice into nothing.
        offset = max(cursor, min(offset, len(text)))
        if offset > cursor:
            content.append({"type": "text", "text": text[cursor:offset]})
        counter[0] += 1
        content.append(_footnote(footnotes[note_id], counter[0]))
        cursor = offset
    if cursor < len(text):
        content.append({"type": "text", "text": text[cursor:]})
    return content


def _section_content(
    blocks: list[Block], footnotes: dict[str, str], counter: list[int]
) -> list[dict]:
    """Block nodes for one section, footnotes inline at their reference points."""
    content: list[dict] = []
    for block in blocks:
        inline = _inline_with_footnotes(block, footnotes, counter)
        if block.depth >= 2:
            content.append(
                {
                    "type": "heading",
                    "attrs": {
                        "level": min(block.depth, 6),
                        "role": _HEADING_ROLE.get(block.depth, "subsubsection"),
                    },
                    "content": inline,
                }
            )
        else:
            content.append({"type": "paragraph", "content": inline})
    return content


def _toc_entry(block: Block, target: str | None) -> dict:
    """One contents line, linked to the section it names when we found it.

    The link is what earns the entry a real page number: the stylesheet prints
    `target-counter(attr(href), page)` after it, so the figure is the page the
    section actually landed on rather than one carried over from Word.
    """
    text_node = {"type": "text", "text": block.text}
    if target is None:
        return {"type": "paragraph", "content": [text_node]}
    return {
        "type": "paragraph",
        "content": [
            {
                "type": "crossReference",
                "attrs": {"target": target, "display": "page"},
                "content": [text_node],
            }
        ],
    }


def _toc_target(text: str, targets: dict[str, str]) -> str | None:
    """The chapter id a contents line points at, by number or by title.

    Only chapters carry an `attrs.id`, so only top-level entries can be linked.
    A subsection entry ("7.1.2 ...") resolves to nothing and prints without a
    page number rather than pointing at the wrong page.
    """
    number = _section_number(text)
    if number and number in targets:
        return targets[number]
    match = NUMBERED_HEADING.match(text) if len(text) <= MAX_NUMBERED_HEADING_CHARS else None
    bare = match.group(2).strip() if match else text.strip()
    return targets.get(_fold_diacritics(bare))


def _section_number(text: str) -> str | None:
    """The "4.3.1" of a numbered line, used to pair a TOC entry to a section."""
    if len(text) > MAX_NUMBERED_HEADING_CHARS:
        return None
    match = NUMBERED_HEADING.match(text)
    return match.group(1) if match else None


def _group_headings(blocks: list[Block]) -> list[tuple[str, list[str]]]:
    """Split blocks into (title, body_paragraphs) sections.

    Consecutive heading candidates collapse into a single title: a display
    title set over three lines ("Β΄ ΕΝΟΤΗΤΑ" / "ΜΗΧΑΝΙΚΗ ΨΥΧΗ" / "ΚΑΙ" /
    "ΕΥΧΑΡΙΣΤΙΑΚΟ ΠΟΤΗΡΙΟ") is one heading, not four empty chapters.

    The leading run before the first heading is returned with an empty title.
    """
    sections: list[tuple[str, list[str]]] = []
    title_parts: list[str] = []
    body: list[str] = []
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
            seen_heading = True
            continue

        if title_parts:
            current_title = " ".join(title_parts)
            title_parts.clear()
        elif not seen_heading:
            current_title = ""
        body.append(block.text)

    if title_parts:
        current_title = " ".join(title_parts)
        title_parts.clear()
    close()

    return sections


def _is_back_matter(title: str) -> bool:
    folded = _fold_diacritics(title)
    return any(p.search(folded) for p in BACK_MATTER_PATTERNS)


def _is_toc_marker(title: str) -> bool:
    # Folded on both sides: the patterns are written in capitals without tonos,
    # and a real contents page is typed "Περιεχόμενα". See _fold_diacritics.
    folded = _fold_diacritics(title)
    return any(p.search(folded) for p in TOC_PATTERNS)


def _group_sections(blocks: list[Block]) -> list[tuple[str, list[Block]]]:
    """Split numbered-mode blocks into (title, body_blocks) sections.

    Only a DEPTH-1 heading opens a section. Deeper numbered headings ("7.1.2")
    stay inside the section they belong to, carried as Blocks so the AST builder
    can emit them as `heading` nodes rather than flattening them to paragraphs.
    Unlike `_group_headings`, consecutive headings are never merged into one
    title: in a numbered document "7.1" following "7" is a subsection, not the
    second line of a display title.
    """
    sections: list[tuple[str, list[Block]]] = []
    title = ""
    body: list[Block] = []

    def close() -> None:
        nonlocal title, body
        if title or body:
            sections.append((title, body))
        title = ""
        body = []

    for block in blocks:
        if block.is_heading_candidate and block.depth == 1:
            close()
            title = block.text
            continue
        body.append(block)

    close()
    return sections


def _find_body_start(sections: list[tuple[str, list[Block]]]) -> int:
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
        _, section_blocks = sections[i]
        if any(len(b.text) >= SUBSTANTIVE_PARAGRAPH_CHARS for b in section_blocks):
            return i

    # No section anywhere clears the prose bar. Treat the first heading as the
    # body rather than emitting a book with no chapters at all.
    return start


def _assert_no_text_lost(
    blocks: list[Block], ast: dict, footnotes: dict[str, str] | None = None
) -> None:
    """Post-condition: no DOCX text was dropped on the way into the AST.

    Two streams, kept apart on purpose. A paragraph that references a footnote
    now has its inline content CUT at the reference point, with the note spliced
    into the gap -- so a single flat concatenation of every text node would read
    "...οποιουσδήποτε μανθάνοντες<the whole note> και σε οποιαδήποτε..." and the
    block's own text would no longer appear in it contiguously. Rejoining each
    block's pieces without the note reconstructs exactly the string
    `paragraph.text` gave us, and the notes are checked as their own stream.

    Weakening this to a substring-of-anything test would have been the easy fix
    and the wrong one: contiguity is what makes it a text-loss check rather than
    a character-set check.
    """
    body_parts: list[str] = []
    note_parts: list[str] = []

    def inline_text(nodes, sink: list[str]) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "text":
                sink.append(node.get("text", ""))
            elif node.get("type") == "footnote":
                inner: list[str] = []
                inline_text(node.get("content"), inner)
                note_parts.append("".join(inner))
            else:
                # emphasis, crossReference, ... -- transparent wrappers whose
                # text belongs to the block that contains them.
                inline_text(node.get("content"), sink)

    def walk(node) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        title = (node.get("attrs") or {}).get("title")
        if title:
            body_parts.append(title)

        kind = node.get("type")
        if kind in ("paragraph", "heading"):
            sink: list[str] = []
            inline_text(node.get("content"), sink)
            body_parts.append("".join(sink))
            return
        if kind == "footnote":
            inner: list[str] = []
            inline_text(node.get("content"), inner)
            note_parts.append("".join(inner))
            return

        for key in ("frontMatter", "body", "backMatter", "content"):
            walk(node.get(key))

    walk(ast)
    haystack = " ".join(p for p in (*body_parts, *note_parts) if p)

    missing = [b.text for b in blocks if b.text not in haystack]
    if missing:
        raise IngestError(
            f"{len(missing)} of {len(blocks)} DOCX blocks did not reach the AST. "
            f"First dropped block: {missing[0][:120]!r}"
        )

    # Footnote text lives in a separate DOCX part, so it is absent from `blocks`
    # and the check above cannot see it. Without this clause 477 notes could go
    # missing with every gate downstream still green -- see read_footnotes.
    lost_notes = [t for t in (footnotes or {}).values() if t not in haystack]
    if lost_notes:
        raise IngestError(
            f"{len(lost_notes)} of {len(footnotes or {})} footnotes did not reach "
            f"the AST. First dropped note: {lost_notes[0][:120]!r}"
        )


def _blank_leaf() -> dict:
    """One reserved leaf at the front of the book.

    A bare `pageBreak` blockNode, which `frontMatterNode` admits directly
    alongside the typed items. It carries no text, so it adds nothing to either
    side of the integrity comparison -- it is structure, and the stylesheet
    turns it into a page with no folio and no running head.
    """
    return {"type": "pageBreak", "attrs": {"breakType": "page"}}


def docx_to_ast(
    path: str | Path,
    *,
    title: str | None = None,
    language: str = "el-GR",
    manuscript_id: str | None = None,
    blank_leading_pages: int = 0,
) -> dict:
    """Convert a DOCX manuscript into an `ast/1` document.

    Raises `IngestError` if any DOCX text would be lost.
    """
    source = Path(path)
    if not source.exists():
        raise IngestError(f"DOCX not found: {source}")

    blocks = read_blocks(source)
    if not blocks:
        raise IngestError(f"DOCX contains no text: {source}")

    footnotes = read_footnotes(source)

    numbered = any(b.depth for b in blocks)
    if numbered:
        sections = _group_sections(blocks)
    else:
        # Legacy path: upper-case/style heading detection, whose grouping merges
        # consecutive heading lines into one display title. `_group_headings`
        # returns plain strings, so lift them back into Blocks for one builder.
        sections = [
            (title, [Block(text, "Normal", False) for text in paragraphs])
            for title, paragraphs in _group_headings(blocks)
        ]

    body_start = _find_body_start(sections)

    # Everything from the contents marker up to the first section with real
    # prose IS the contents page -- in a numbered document each of its entries
    # is itself a numbered line, so grouping gives every entry its own empty
    # section. Merged back into one `toc` item here rather than emitted as two
    # dozen single-line front-matter blocks.
    toc_start = next(
        (i for i, (title, _) in enumerate(sections) if _is_toc_marker(title)),
        None,
    )

    front_matter: list[dict] = []
    body: list[dict] = []
    back_matter: list[dict] = []
    chapter_number = 0
    footnote_counter = [0]

    # Pass 1: which section number does each chapter carry? The TOC entries are
    # paired to chapter ids by that number, so a contents line can name the page
    # its section actually starts on.
    # Keyed by section number AND by folded title, because a contents entry and
    # the section it names do not always agree about carrying the number -- see
    # the note in `_apply_numbering`.
    targets: dict[str, str] = {}
    pending = 0
    for index, (section_title, _) in enumerate(sections):
        if index < body_start or _is_back_matter(section_title):
            continue
        pending += 1
        chapter_id = f"ch{pending}"
        number = _section_number(section_title)
        if number:
            targets.setdefault(number, chapter_id)
        match = NUMBERED_HEADING.match(section_title)
        bare = match.group(2).strip() if match else section_title
        if bare:
            targets.setdefault(_fold_diacritics(bare), chapter_id)

    toc_blocks: list[Block] = []

    for index, (section_title, section_blocks) in enumerate(sections):
        if index < body_start:
            if toc_start is not None and index >= toc_start:
                # Accumulate; emitted as a single `toc` item after the loop.
                if section_title:
                    toc_blocks.append(Block(section_title, "Normal", False))
                toc_blocks.extend(section_blocks)
                continue
            # Front matter carries no `attrs.title` -- see module docstring --
            # so its heading survives as a leading paragraph instead.
            content = _section_content(section_blocks, footnotes, footnote_counter)
            if section_title:
                content.insert(0, _paragraph(section_title))
            front_matter.append({"type": FRONT_MATTER_TYPE, "content": content})
            continue

        content = _section_content(section_blocks, footnotes, footnote_counter)

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

    if blank_leading_pages:
        # Prepended, so they precede even the half title. Reserved for the
        # material a publisher sets last (half title, title, copyright,
        # dedication); the manuscript does not supply it and the pipeline must
        # not invent it, so they are left empty.
        front_matter[:0] = [_blank_leaf() for _ in range(blank_leading_pages)]

    if toc_blocks:
        # Inserted at the contents page's own position in the front matter, not
        # appended, so the book keeps the order the author typed.
        entries = [_toc_entry(b, _toc_target(b.text, targets)) for b in toc_blocks]
        front_matter.append({"type": "toc", "content": entries})

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

    _assert_no_text_lost(blocks, ast, footnotes)

    all_text = " ".join(b.text for b in blocks)
    ast["integrityHash"] = "sha256:" + hashlib.sha256(
        all_text.encode("utf-8")
    ).hexdigest()

    return ast
