"""
The text stream of a Book AST -- one side of every text-integrity comparison.

`ast-assemble` compares it with `extract`'s HTML (the gate every book passes
before it can render), and `epub` with the EPUB it is about to store. It lives
here, not in either stage, so the two check against the same definition of
"the book's text": a second copy could drift and let one of them pass a loss
the other would catch.
"""

from __future__ import annotations

# ast.schema.json's `inlineNode` types: they sit inside a block's text and are
# rendered inline, so no boundary goes between them.
INLINE_TYPES = frozenset({
    "text", "emphasis", "strong", "link", "superscript", "subscript", "smallCaps",
    "codeInline", "hardBreak", "indexEntry", "crossReference",
})


def ast_text(ast: dict) -> str:
    """Extract and concatenate all text content from a canonical AST (schemas/ast),
    walking frontMatter/body/backMatter.

    Chapter and section titles live in `attrs.title`, not as a `type: "text"` node
    inside `content` -- the original version of this function only walked `content`
    arrays, so it silently dropped every chapter title from the integrity check.
    `stages.rendering.ast_to_html` DOES render titles into the HTML (as an
    `<h1>`), so omitting them here made the two sides of the comparison
    structurally unequal even for a perfectly faithful conversion.

    Key order below is NOT arbitrary: it must match `ast_to_html`'s render order
    (frontMatter, then body, then backMatter). The root AST dict has all three keys
    simultaneously, so walking them in a different order -- the original code used
    ("content", "frontMatter", "backMatter", "body"), putting backMatter before
    body -- silently reorders the concatenated text relative to what the HTML
    actually rendered, and the comparison fails on ANY manuscript with non-empty
    back matter even when no text was lost or altered.

    Text runs inside one block are concatenated with NOTHING between them, and a
    space goes only at block boundaries. `ast_to_html` renders marked runs back to
    back (`(<em>word</em>,`), so a separator between every text node -- which this
    used to insert -- made the source side read "( word ," against the HTML's
    "(word,". The first real manuscript (polytonic Greek citations in italics
    between brackets) failed the gate at offset 262 with no text lost at all.
    """
    texts: list[str] = []

    def _walk(node):
        if isinstance(node, dict):
            kind = node.get("type")
            if kind == "text":
                texts.append(node.get("text", ""))
                return
            block = kind not in INLINE_TYPES
            if block:
                texts.append(" ")
            attrs = node.get("attrs") or {}
            # Rendered text that lives in attrs, in render order: a title or a
            # caption (`<caption>` precedes a table's rows; a figure has no
            # content) before the content, an epigraph's source after it.
            # Captions and sources used to be skipped, so the first figure or
            # table ingest captioned would have failed the gate with nothing lost.
            for key in ("title", "caption"):
                if attrs.get(key):
                    texts.extend((" ", attrs[key], " "))
            for key in ("frontMatter", "body", "backMatter", "content"):
                val = node.get(key)
                if isinstance(val, list):
                    for item in val:
                        _walk(item)
                elif isinstance(val, dict):
                    _walk(val)
            if kind == "epigraph" and attrs.get("source"):
                texts.extend((" ", attrs["source"], " "))
            if block:
                texts.append(" ")
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(ast)
    return "".join(texts)


def divergence(rendered: str, source: str) -> int:
    """The offset where two normalized streams first differ."""
    i, limit = 0, min(len(rendered), len(source))
    while i < limit and rendered[i] == source[i]:
        i += 1
    return i


def excerpt(text: str, at: int) -> str:
    return repr(text[max(0, at - 40):at + 40])
