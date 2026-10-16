"""
Presentation MathML as the Book AST carries it (`ast.schema.json` `mathml`): the
one definition of what an equation may hold and of its text.

An equation's text is the text of its elements in document order (`math_text`).
The integrity gate reads it three ways -- `text_stream.ast_text` calls
`math_text`, `parse_html` and the EPUB's `spine_text` read the rendered element's
character data -- and those agree only because the MathML is compact (no
whitespace between elements) and holds nothing that is not printed. `parse`
refuses anything else, so the three cannot drift:

- an element outside `ELEMENTS` (`mphantom` draws nothing, so its text would
  pass the gate unprinted; `annotation` is text no renderer prints; anything
  outside MathML is markup injected into the page);
- an attribute outside `ATTRIBUTES` (no event handlers, no `href`);
- whitespace between elements, or an invisible operator (U+2061-U+2064): text
  one side of the gate would count and a reader never sees;
- a DOCTYPE or entity declaration (the string is parsed by expat).

`render` re-serializes what `parse` accepted, so a renderer never copies the
stored string into a page.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

ELEMENTS = frozenset({
    "math", "mrow", "mi", "mn", "mo", "mtext", "ms", "mspace",
    "msup", "msub", "msubsup", "mmultiscripts", "mprescripts", "none",
    "mfrac", "msqrt", "mroot", "mover", "munder", "munderover",
    "mtable", "mtr", "mtd", "mstyle", "mpadded", "menclose",
})
ATTRIBUTES = frozenset({
    "display", "mathvariant", "stretchy", "fence", "separator", "form", "accent",
    "accentunder", "largeop", "movablelimits", "symmetric", "lspace", "rspace",
    "linethickness", "notation", "columnalign", "rowalign", "displaystyle",
    "scriptlevel", "width", "height", "depth", "alttext",
})
INVISIBLE = re.compile("[⁡-⁤]")
_DECLARATION = re.compile(r"<!(DOCTYPE|ENTITY)", re.IGNORECASE)


class MathMLError(ValueError):
    """The string is not MathML the Book AST may carry."""


def parse(mathml: str) -> ET.Element:
    """The `<math>` element `mathml` holds, checked against the rules above."""
    if _DECLARATION.search(mathml):
        raise MathMLError("MathML may not declare a DOCTYPE or entities")
    try:
        root = ET.fromstring(mathml)
    except ET.ParseError as exc:
        raise MathMLError(f"not well-formed MathML: {exc}") from exc
    if root.tag != f"{{{MATHML_NS}}}math":
        raise MathMLError(f"the root must be <math> in the MathML namespace, not {root.tag!r}")
    for el in root.iter():
        name = el.tag.removeprefix(f"{{{MATHML_NS}}}")
        if name == el.tag or name not in ELEMENTS:
            raise MathMLError(f"element {el.tag!r} is not allowed in an equation")
        for attr in el.attrib:
            if attr not in ATTRIBUTES or (attr == "alttext" and el is not root):
                raise MathMLError(f"attribute {attr!r} is not allowed on <{name}>")
        for text in (el.text, el.tail if el is not root else None):
            if text and INVISIBLE.search(text):
                raise MathMLError("an invisible operator (U+2061-U+2064) is text no reader sees")
        if len(el) and (el.text or any(child.tail for child in el)):
            raise MathMLError(f"<{name}> mixes text and elements, or has whitespace between them")
    return root


def math_text(mathml: str) -> str:
    """The equation's text, as the integrity gate reads it."""
    return "".join(parse(mathml).itertext())


def _escape(text: str, quote: bool = False) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace('"', "&quot;") if quote else text


def _serialize(el: ET.Element) -> str:
    name = el.tag.removeprefix(f"{{{MATHML_NS}}}")
    attrs = "".join(f' {k}="{_escape(v, True)}"' for k, v in sorted(el.attrib.items()))
    inner = _escape(el.text or "") + "".join(_serialize(child) for child in el)
    return f"<{name}{attrs}>{inner}</{name}>"


def render(mathml: str, display: bool) -> str:
    """`mathml`, checked and re-serialized for a page: `display` sets it as a
    block (`equation`) or inside the line (`math`)."""
    root = parse(mathml)
    root.attrib.pop("display", None)
    if display:
        root.set("display", "block")
    out = _serialize(root)
    return f'<math xmlns="{MATHML_NS}"' + out[len("<math"):]


__all__ = ["ATTRIBUTES", "ELEMENTS", "MATHML_NS", "MathMLError", "math_text", "parse", "render"]
