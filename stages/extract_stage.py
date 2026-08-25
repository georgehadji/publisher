"""
Tracer Bullet -- extract stage.

Converts a manuscript fixture (AST JSON) into a "typescript HTML" representation.
In the tracer bullet, this reads the synthetic AST and produces HTML fragments.
For real production, this would use Saxon/XSweet on actual DOCX files.
"""

from __future__ import annotations
import json
import unicodedata
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType, ArtifactRef


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<title>{title}</title>
</head>
<body>
{body}
</body>
</html>"""


# Combining marks that Greek DROPS when a word is set in capitals: the tonos
# and its polytonic ancestors (oxia, varia, perispomeni), the breathings, and
# the iota subscript. The dialytika (U+0308) is NOT in this set -- it is the
# one Greek diacritic that survives capitalisation, because it marks a vowel
# pair that must be read separately and the capitals do not make that clearer.
_GREEK_CAPS_DROP = {
    "\u0300",  # varia / grave
    "\u0301",  # oxia / tonos / acute
    "\u0313",  # psili
    "\u0314",  # dasia
    "\u0342",  # perispomeni
    "\u0343",  # koronis
    "\u0345",  # ypogegrammeni
}


def _upper_for_lang(text: str, lang: str) -> str:
    """Uppercase `text` under the casing rules of `lang`.

    CSS `text-transform: uppercase` is not usable here. WeasyPrint implements
    it with Python's `str.upper()`, which keeps the tonos: "Περιεχόμενα" comes
    out "ΠΕΡΙΕΧΌΜΕΝΑ". Accented capitals are a spelling error in Greek -- the
    accent is dropped in all-caps setting -- and a running head repeats on
    every page, so the mistake would have appeared several hundred times.

    Only Greek gets the stripping. French, Spanish and German all keep their
    accents in capitals, so for every other language this is a plain upcase.
    """
    upper = text.upper()
    if not lang.lower().startswith("el"):
        return upper
    decomposed = unicodedata.normalize("NFD", upper)
    kept = "".join(c for c in decomposed if c not in _GREEK_CAPS_DROP)
    return unicodedata.normalize("NFC", kept)


def _ast_to_html(ast: dict) -> str:
    """Convert a Book AST into a flat typescript-like HTML document.
    
    This is deliberately naive -- the tracer bullet validates the contract,
    not the quality. Production will use XSweet.
    """
    parts = []
    
    # Title from metadata
    title = (ast.get("metadata") or {}).get("title", "Untitled")
    # Bound here rather than beside the template call at the bottom: the
    # chapter loop below needs it to build each running head's caps form.
    language = (ast.get("metadata") or {}).get("language") or "en"
    
    # Process front matter
    front_matter = ast.get("frontMatter") or []
    for item in front_matter:
        # `frontMatterNode` admits a bare blockNode as well as the typed
        # {type, content} items -- a reserved blank leaf is a lone `pageBreak`.
        # Wrapping one in `.front-matter` would give it that class's
        # `break-before: recto` and turn each reserved leaf into two pages.
        if "content" not in item:
            parts.append(_render_content([item]))
            continue
        parts.append(f'<div class="front-matter {item.get("type", "unknown")}">')
        parts.append(_render_content(item.get("content", [])))
        parts.append("</div>")
    
    # Process body (chapters)
    body = ast.get("body", [])
    for chapter in body:
        ctype = chapter.get("type", "unknown")
        attrs = chapter.get("attrs", {})
        cid = attrs.get("id", f"{ctype}-{attrs.get('number', '?')}")
        title_text = attrs.get("title", f"Chapter {attrs.get('number', '?')}")
        
        parts.append(f'<div class="{ctype}" id="{cid}" data-number="{attrs.get("number", "")}">')
        # `data-caps` is the running head's copy of the title, upcased here
        # rather than in CSS so Greek loses its tonos -- see `_upper_for_lang`.
        # The stylesheet takes `string-set` from this attribute when the
        # DesignSpec asks for uppercase heads, and from the element text
        # otherwise; the printed chapter title itself is never transformed.
        caps = _upper_for_lang(title_text, language)
        parts.append(
            f'<h1 class="chapter-title" data-caps="{_escape_html(caps)}">'
            f'{_escape_html(title_text)}</h1>'
        )
        parts.append(_render_content(chapter.get("content", [])))
        parts.append("</div>")
    
    # Process back matter
    back_matter = ast.get("backMatter") or []
    for item in back_matter:
        parts.append(f'<div class="back-matter {item.get("type", "unknown")}">')
        parts.append(_render_content(item.get("content", [])))
        parts.append("</div>")
    
    return HTML_TEMPLATE.format(
        title=_escape_html(title),
        body="\n".join(parts),
        lang=_escape_html(language),
    )


def _render_content(content: list) -> str:
    """Render AST block content to HTML."""
    parts = []
    for node in content:
        ntype = node.get("type", "unknown")
        
        if ntype == "paragraph":
            pattrs = node.get("attrs") or {}
            role = pattrs.get("role", "normal")
            cls = f"paragraph {role}" if role != "normal" else "paragraph"
            # `attrs.indent` has been in the AST schema from the start and was
            # never rendered. A contents page uses it to step its subsection
            # entries in under the chapter they belong to.
            indent = pattrs.get("indent")
            style = f' style="padding-left: {float(indent):g}em"' if indent else ""
            parts.append(
                f'<p class="{cls}"{style}>{_render_inline(node.get("content", []))}</p>'
            )
        
        elif ntype == "heading":
            hattrs = node.get("attrs") or {}
            level = hattrs.get("level", 2)
            # The anchor is what earns a subsection its page number: the
            # contents entry that names it prints `target-counter(attr(href),
            # page)`, which resolves to nothing unless the target exists.
            hid = hattrs.get("id")
            anchor = f' id="{_escape_html(hid)}"' if hid else ""
            parts.append(
                f'<h{level}{anchor}>{_render_inline(node.get("content", []))}</h{level}>'
            )
        
        elif ntype == "blockquote":
            parts.append(f'<blockquote>{_render_content(node.get("content", []))}</blockquote>')
        
        elif ntype == "epigraph":
            source = (node.get("attrs") or {}).get("source", "")
            inner = _render_content(node.get("content", []))
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
            inner = _render_content(node.get("content", []))
            parts.append(f'<div class="dialogue" data-speaker="{_escape_html(speaker)}">{inner}</div>')
        
        elif ntype == "list":
            list_type = (node.get("attrs") or {}).get("listType", "unordered")
            tag = "ol" if list_type == "ordered" else "ul"
            parts.append(f'<{tag}>')
            for item in node.get("content", []):
                parts.append(f'<li>{_render_content(item.get("content", []))}</li>')
            parts.append(f'</{tag}>')
        
        elif ntype == "figure":
            attrs = node.get("attrs", {})
            caption = attrs.get("caption", "")
            parts.append('<figure>')
            parts.append(f'<figcaption>{_escape_html(caption)}</figcaption>' if caption else '')
            parts.append('</figure>')
        
        elif ntype == "sidebar":
            parts.append(f'<aside class="sidebar">{_render_content(node.get("content", []))}</aside>')
        
        elif ntype == "footnote":
            # Rendered inline, immediately after the block that referenced it,
            # and pulled to the foot of the page by `float: footnote` in the
            # stylesheet. It must stay HERE in document order: `ast-assemble`
            # compares this HTML's text stream against the AST's, so moving the
            # note in the markup -- collecting them at the end, say -- fails the
            # integrity gate even though nothing was lost.
            parts.append(
                f'<span class="footnote">{_render_inline(node.get("content", []))}</span>'
            )

        elif ntype == "pageBreak":
            parts.append('<div class="page-break"></div>')
        
        elif ntype in ("halfTitle", "titlePage", "copyrightPage", "dedication", "toc", "foreword", 
                       "preface", "acknowledgments", "prologue", "epilogue", "afterword", 
                       "appendix", "notes", "bibliography", "index", "aboutTheAuthor", "alsoBy", "colophon"):
            role = ntype
            parts.append(f'<div class="{role}">{_render_content(node.get("content", []))}</div>')
        
        elif ntype == "table":
            parts.append(_render_table(node))
        
        else:
            parts.append(f'<!-- unknown node type: {ntype} -->')
    
    return "\n".join(parts)


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
                    text = f'<a href="{_escape_html(href)}">{text}</a>'
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

        elif ntype == "footnote":
            # Inline here, at the reference point, so WeasyPrint puts the call
            # exactly where the author put it. Same markup as the block-level
            # branch; only the position differs.
            parts.append(
                f'<span class="footnote">{_render_inline(node.get("content", []))}</span>'
            )

        elif ntype == "crossReference":
            # `href` is what earns the entry its page number: the stylesheet
            # prints target-counter(attr(href), page) after it, so the figure is
            # the page the target actually landed on. Without the anchor there is
            # nothing for target-counter to resolve.
            # A <span> carrying an href, deliberately NOT an <a>. CSS attr()
            # reads the attribute off any element, so target-counter resolves
            # either way -- but an <a href> also makes WeasyPrint emit a /Link
            # ANNOTATION, and PDF/X-1a forbids those. Ghostscript does not warn
            # and downgrade one annotation; it abandons PDF/X for the whole file
            # ("not permitted in PDF/X, reverting to normal PDF output"), which
            # `finish` then refuses outright. A press file has no clickable
            # links to lose.
            target = (node.get("attrs") or {}).get("target", "")
            inner = _render_inline(node.get("content", []))
            parts.append(
                f'<span class="xref" href="#{_escape_html(target)}">{inner}</span>'
            )
        
        else:
            parts.append(_escape_html(str(node.get("text", ""))))
    
    return "".join(parts)


def _render_table(node: dict) -> str:
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
            parts.append(f'<{tag} colspan="{colspan}">{_render_content(cell.get("content", []))}</{tag}>')
        parts.append("</tr>")
    parts.append("</table>")
    return "\n".join(parts)


def _escape_html(text: str) -> str:
    import html as _html
    return _html.escape(text, quote=True)


@stage(
    name="extract",
    # v2: renders `footnote` and `crossReference` nodes. v1 hit neither branch
    # and fell through to its `unknown node type` comment / empty-string default,
    # so any AST carrying them lost that text on the way into the HTML. Every v1
    # artifact for a manuscript with footnotes is therefore incomplete and must
    # not be replayed from cache.
    # v3: chapter titles carry `data-caps` (the running head's correctly-cased
    # Greek uppercase, which CSS `text-transform` cannot produce), headings carry
    # their `attrs.id` so a contents entry can resolve a page number against
    # them, and `attrs.indent` is finally rendered. A v2 HTML has no anchors, so
    # every subsection line in its contents prints without a page number.
    version=3,
    inputs={"source": "raw-source/1"},
    outputs={"html": "typescript-html/1"},
    toolchain=[],
    fixtures="fixtures/extract/v1",
    memory_budget_mb=128,
    queue="q.ingest",
    description="Convert manuscript fixture to typescript HTML",
)
def extract(ctx: StageCtx, source: str | None = None) -> StageResult:
    """
    Extract stage: loads AST JSON from CAS, converts to flat HTML.
    
    In the tracer bullet, source is a path to an AST JSON file,
    or a fixture path.
    """
    if source is None:
        # Default fixture for tracer bullet
        source = "corpus/manuscripts/minimal-novel.ast.json"
    
    fixture_path = Path(source)
    if not fixture_path.exists():
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"Source not found: {source}",
        )
    
    ast = json.loads(fixture_path.read_bytes())
    html = _ast_to_html(ast)
    
    # Store in CAS
    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(html.encode("utf-8"), media_type=MediaType("text/html"))
    
    print(f"  [extract] Generated HTML from {fixture_path.name} -> {ref.hash} ({len(html)} chars)")
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="html",   # must exactly equal the declared output key "html"
            hash=str(ref.hash),
            media_type="text/html",
            size=len(html),
        )],
        metrics={"html_size_chars": len(html), "paragraph_count": html.count("<p ")},
    )
