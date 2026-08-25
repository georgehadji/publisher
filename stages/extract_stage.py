"""
Tracer Bullet -- extract stage.

Converts a manuscript fixture (AST JSON) into a "typescript HTML" representation.
In the tracer bullet, this reads the synthetic AST and produces HTML fragments.
For real production, this would use Saxon/XSweet on actual DOCX files.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType, ArtifactRef


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


def _ast_to_html(ast: dict) -> str:
    """Convert a Book AST into a flat typescript-like HTML document.
    
    This is deliberately naive -- the tracer bullet validates the contract,
    not the quality. Production will use XSweet.
    """
    parts = []
    
    # Title from metadata
    title = (ast.get("metadata") or {}).get("title", "Untitled")
    
    # Process front matter
    front_matter = ast.get("frontMatter") or []
    for item in front_matter:
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
        parts.append(f'<h1 class="chapter-title">{_escape_html(title_text)}</h1>')
        parts.append(_render_content(chapter.get("content", [])))
        parts.append("</div>")
    
    # Process back matter
    back_matter = ast.get("backMatter") or []
    for item in back_matter:
        parts.append(f'<div class="back-matter {item.get("type", "unknown")}">')
        parts.append(_render_content(item.get("content", [])))
        parts.append("</div>")
    
    return HTML_TEMPLATE.format(title=_escape_html(title), body="\n".join(parts))


def _render_content(content: list) -> str:
    """Render AST block content to HTML."""
    parts = []
    for node in content:
        ntype = node.get("type", "unknown")
        
        if ntype == "paragraph":
            role = (node.get("attrs") or {}).get("role", "normal")
            cls = f"paragraph {role}" if role != "normal" else "paragraph"
            parts.append(f'<p class="{cls}">{_render_inline(node.get("content", []))}</p>')
        
        elif ntype == "heading":
            level = (node.get("attrs") or {}).get("level", 2)
            parts.append(f'<h{level}>{_render_inline(node.get("content", []))}</h{level}>')
        
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
            # An inline element at block position on purpose: `float: footnote`
            # (CSS Generated Content for Paged Media) moves it into the page's
            # footnote area and numbers the call itself. Rendering it as a block
            # would print the note inline in the text where it happens to sit.
            parts.append(
                f'<span class="footnote">{_render_inline(node.get("content", []))}</span>'
            )
        
        elif ntype == "sidebar":
            parts.append(f'<aside class="sidebar">{_render_content(node.get("content", []))}</aside>')
        
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


@stage(
    name="extract",
    version=1,
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
