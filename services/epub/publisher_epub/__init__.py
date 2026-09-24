"""
EPUB 3 Writer -- rendered sections -> an EPUB 3 package.

From BUILD_PLAN.md §3.12: EPUB 3 + a11y, validated by EPUBCheck AND ACE by DAISY.

This writer packages; it does not render. It used to walk the AST itself and
knew paragraphs, blockquotes and headings only: every footnote, table, figure,
sidebar and front/back-matter section and every mark was dropped, and text
after an inline element was moved in front of it -- the first real book's EPUB
held 76% of its text, and nothing checked. The content documents now come from
the renderer the print path's integrity gate verifies (`stages/rendering.py`'s
`ast_to_epub_sections`), the same split `publisher_idml` has with pandoc.

`spine_text` reads a package back in reading order, which is what lets the
`epub` stage prove the stored file still carries the book's text.
"""

from __future__ import annotations

import os
import posixpath
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
OPF_NS = "http://www.idpf.org/2007/opf"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"

# EPUB 3 core media types for images. Anything else is a "foreign resource"
# that needs a fallback chain; the caller converts instead (the epub stage
# turns TIFF into PNG), and this writer refuses what it is still handed.
CORE_IMAGE_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/svg+xml", "image/webp"})

# Every zip entry gets this timestamp: the package is content-addressed, and a
# wall-clock mtime in each entry would give every build of the same book a
# different hash.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

STYLESHEET = """\
@namespace epub "http://www.idpf.org/2007/ops";
body { margin: 0 5%; line-height: 1.4; }
p { margin: 0; text-indent: 1.5em; text-align: justify; }
.chapter-title { text-align: center; margin: 2em 0 1em; page-break-before: always; }
blockquote { margin: 0.5em 1.5em; font-style: italic; }
blockquote.epigraph footer { text-align: right; font-size: 0.9em; }
.verse { margin-left: 2em; font-style: italic; }
.verse-line { text-indent: -1em; padding-left: 1em; }
hr.scene-break { border: none; margin: 1em 0; text-align: center; }
hr.scene-break::before { content: '* * *'; }
pre.code-block { font-family: monospace; font-size: 0.85em; white-space: pre-wrap; }
table { border-collapse: collapse; margin: 0.5em 0; }
th, td { border: 1px solid #999; padding: 0.2em 0.5em; text-align: left; }
figure { margin: 1em 0; text-align: center; }
figure img { max-width: 100%; }
figcaption { font-size: 0.85em; font-style: italic; }
aside.sidebar { border: 1px solid #999; padding: 0.5em 1em; margin: 1em 0; }
a[epub|type~='noteref'] { vertical-align: super; font-size: 0.7em; text-decoration: none; }
aside[epub|type~='footnote'] { font-size: 0.85em; }
aside[epub|type~='footnote'] p { text-indent: 0; }
.small-caps { font-variant: small-caps; }
"""


class EPUBError(ValueError):
    """The package cannot be written as asked (malformed content, unusable media)."""


@dataclass(frozen=True)
class Media:
    media_type: str
    data: bytes


def _reproducible_utc(explicit: str | None = None) -> datetime:
    """Timestamp for embedding in a delivered artifact -- never the wall clock."""
    if explicit:
        return datetime.fromisoformat(explicit.replace("Z", "+00:00"))
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    return datetime.fromtimestamp(int(epoch) if epoch else 0, tz=timezone.utc)


def _identifier(metadata: Mapping) -> str:
    isbn = metadata.get("isbn")
    if isbn:
        return f"urn:isbn:{isbn}"
    # Stable across rebuilds of the same book, which a random UUID would not be.
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'publisher:' + str(metadata.get('title', '')))}"


class EPUB3Writer:
    """
    Packages rendered sections as an EPUB 3.

    `sections`: dicts with `id`, `title`, `matter` ("frontmatter" / "bodymatter"
    / "backmatter") and `body` -- the markup inside `<body>`, which must be
    well-formed XML (it is parsed before it is written, and a section that does
    not parse raises `EPUBError` rather than producing a package readers reject).
    `media`: href (relative to the package documents, e.g. `media/<sha>.png`) ->
    `Media`.

    Layout: mimetype, META-INF/container.xml, OEBPS/content.opf, OEBPS/nav.xhtml,
    OEBPS/book.css, OEBPS/<section id>.xhtml, OEBPS/media/...
    """

    def __init__(self, sections: Sequence[Mapping], *, metadata: Mapping | None = None,
                 media: Mapping[str, Media] | None = None, stylesheet: str = STYLESHEET,
                 modified: str | None = None):
        if not sections:
            raise EPUBError("an EPUB needs at least one content document")
        self.sections = list(sections)
        self.metadata = dict(metadata or {})
        self.media = dict(media or {})
        self.stylesheet = stylesheet
        self.language = self.metadata.get("language") or "und"
        self._modified = _reproducible_utc(modified)
        for href, item in self.media.items():
            if item.media_type not in CORE_IMAGE_TYPES:
                raise EPUBError(f"{href}: {item.media_type} is not an EPUB core media type")

    def write(self, output_path: str | Path) -> Path:
        output_path = Path(output_path)
        if output_path.suffix != ".epub":
            output_path = output_path.with_suffix(".epub")

        documents = {f"{s['id']}.xhtml": self._content_document(s) for s in self.sections}

        with zipfile.ZipFile(output_path, "w") as zf:
            # The mimetype entry must come first and be STORED, or reading
            # systems (and EPUBCheck) do not recognise the file as an EPUB.
            self._put(zf, "mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
            self._put(zf, "META-INF/container.xml", self._container())
            self._put(zf, "OEBPS/content.opf", self._opf())
            self._put(zf, "OEBPS/nav.xhtml", self._nav())
            self._put(zf, "OEBPS/book.css", self.stylesheet.encode("utf-8"))
            for name, data in documents.items():
                self._put(zf, f"OEBPS/{name}", data)
            for href, item in sorted(self.media.items()):
                self._put(zf, f"OEBPS/{href}", item.data)
        return output_path

    @staticmethod
    def _put(zf: zipfile.ZipFile, name: str, data: bytes,
             compression: int = zipfile.ZIP_DEFLATED) -> None:
        info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
        info.compress_type = compression
        zf.writestr(info, data)

    def _xhtml(self, title: str, body: str, body_type: str = "") -> str:
        lang = quoteattr(self.language)
        epub_type = f" epub:type={quoteattr(body_type)}" if body_type else ""
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" xml:lang={lang} lang={lang}>\n'
            f'<head><meta charset="utf-8"/><title>{escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="book.css"/></head>\n'
            f'<body{epub_type}>\n{body}\n</body>\n</html>\n'
        )

    def _content_document(self, section: Mapping) -> bytes:
        doc = self._xhtml(section.get("title", ""), section["body"], section.get("matter", ""))
        try:
            ET.fromstring(doc.encode("utf-8"))
        except ET.ParseError as exc:
            raise EPUBError(f"section {section['id']} is not well-formed XHTML: {exc}") from exc
        for href in _image_refs(doc):
            if href not in self.media:
                raise EPUBError(f"section {section['id']} shows {href}, which was not supplied")
        return doc.encode("utf-8")

    def _container(self) -> bytes:
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<container version="1.0" xmlns="{CONTAINER_NS}"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>\n'
        ).encode("utf-8")

    def _opf(self) -> bytes:
        md = self.metadata
        title = md.get("title") or "Untitled"
        meta = [
            f'<dc:identifier id="book-id">{escape(_identifier(md))}</dc:identifier>',
            f"<dc:title>{escape(title)}</dc:title>",
            f"<dc:language>{escape(self.language)}</dc:language>",
            f'<meta property="dcterms:modified">{self._modified.strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>',
        ]
        for contributor in md.get("contributors") or []:
            name = contributor.get("displayName") or contributor.get("name")
            if name:
                meta.append(f"<dc:creator>{escape(name)}</dc:creator>")
        # EPUB Accessibility 1.1 discovery metadata -- what ACE checks for first.
        modes = ["textual"] + (["visual"] if self.media else [])
        meta += [f'<meta property="schema:accessMode">{m}</meta>' for m in modes]
        meta += [
            '<meta property="schema:accessModeSufficient">textual</meta>',
            '<meta property="schema:accessibilityFeature">structuralNavigation</meta>',
            '<meta property="schema:accessibilityFeature">tableOfContents</meta>',
            '<meta property="schema:accessibilityHazard">none</meta>',
        ]

        items = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="css" href="book.css" media-type="text/css"/>',
        ]
        items += [f'<item id="{s["id"]}" href="{s["id"]}.xhtml" media-type="application/xhtml+xml"/>'
                  for s in self.sections]
        items += [f'<item id="img{n}" href={quoteattr(href)} media-type="{item.media_type}"/>'
                  for n, (href, item) in enumerate(sorted(self.media.items()), 1)]
        spine = [f'<itemref idref="{s["id"]}"/>' for s in self.sections]

        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<package xmlns="{OPF_NS}" version="3.0" unique-identifier="book-id" '
            'prefix="schema: http://schema.org/">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' + "\n".join(meta) +
            "\n</metadata>\n<manifest>\n" + "\n".join(items) +
            "\n</manifest>\n<spine>\n" + "\n".join(spine) + "\n</spine>\n</package>\n"
        ).encode("utf-8")

    def _nav(self) -> bytes:
        entries = "".join(f'<li><a href="{s["id"]}.xhtml">{escape(s.get("title", ""))}</a></li>'
                          for s in self.sections)
        body = f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{entries}</ol></nav>'
        return self._xhtml("Contents", body).encode("utf-8")


def _image_refs(document: str) -> list[str]:
    root = ET.fromstring(document.encode("utf-8"))
    return [img.get("src", "") for img in root.iter(f"{{{XHTML_NS}}}img")]


# Elements that are blocks in the content documents. A space goes around each,
# as the AST's text stream puts one at every block boundary: a note's aside
# follows its paragraph with no whitespace between them in the markup, and
# without this the two read as one run ("cited.The note").
BLOCKS = frozenset(f"{{{XHTML_NS}}}{tag}" for tag in (
    "section", "div", "p", "aside", "blockquote", "footer", "figure", "figcaption",
    "table", "caption", "tr", "th", "td", "ul", "ol", "li", "pre", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
))


def _element_text(element: ET.Element, out: list[str]) -> None:
    """Document-order text, leaving out `<head>` and the note calls the renderer
    generated (`epub:type="noteref"`): they are numbering, not the book's text.
    Their tails are kept -- that is the prose after the call."""
    tag = element.tag
    skip = tag == f"{{{XHTML_NS}}}head" or "noteref" in (element.get(f"{{{EPUB_NS}}}type") or "").split()
    block = tag in BLOCKS
    if not skip:
        out.append(" " if block else "")
        out.append(element.text or "")
        for child in element:
            _element_text(child, out)
        out.append(" " if block else "")
    out.append(element.tail or "")


def spine_text(epub_path: str | Path) -> str:
    """The text of an EPUB's spine documents, in reading order, one space
    between documents (the block boundary the AST's text stream puts there)."""
    with zipfile.ZipFile(epub_path) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile").get("full-path")
        base = posixpath.dirname(rootfile)
        opf = ET.fromstring(zf.read(rootfile))
        hrefs = {item.get("id"): item.get("href")
                 for item in opf.iter(f"{{{OPF_NS}}}item")}
        parts: list[str] = []
        for ref in opf.iter(f"{{{OPF_NS}}}itemref"):
            document = ET.fromstring(zf.read(posixpath.join(base, hrefs[ref.get("idref")])))
            _element_text(document, parts)
            parts.append(" ")
    return "".join(parts)


__all__ = ["CORE_IMAGE_TYPES", "EPUB3Writer", "EPUBError", "Media", "spine_text"]
