"""
Word's equations (OMML, `m:oMath`) as presentation MathML -- the form the Book
AST carries (`ast.schema.json` `mathml`, `publisher_structure.mathml`).

OMML is a tree of structures (`m:f` a fraction, `m:nary` a sum or integral, ...)
over runs of text (`m:r`/`m:t`). Each structure maps onto one MathML element;
each run's characters become tokens: a number (`mn`), a letter (`mi`, one per
letter, as Word sets them), anything else an operator (`mo`). A run Word marks
as ordinary text (`m:nor`) stays one `mtext`.

Word keeps some glyphs in properties, not in `m:t`: the brackets of `m:d`
(`m:begChr`/`m:endChr`, default parentheses), the operator of `m:nary` (`m:chr`,
default an integral) and of an accent or a grouping character. The MathML holds
them as `mo`, so its text holds every character of the equation's `m:t` text --
what ingest's no-loss check holds it to. Not in the same order: a root's degree
precedes its base in OMML and follows it in MathML (`mroot`), and prescripts
likewise.

Anything this module does not know is refused by name (`UnsupportedMath`), never
skipped: an equation with part of it missing reads as complete. `m:phant`
(phantom: space for content that is not drawn) is refused too, since its text
would reach the integrity gate without being printed.
"""

from __future__ import annotations

import re
from typing import Callable

from lxml import etree

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"

# Elements that carry no content: properties, bookmarks, proofing marks.
_SKIP = re.compile(rf"^({re.escape(M)}\w+Pr|{re.escape(M)}ctrlPr|{re.escape(W)}(rPr|bookmarkStart|bookmarkEnd|proofErr|permStart|permEnd))$")
# Revision wrappers whose content is the document's (as docx_rich.TRANSPARENT).
_TRANSPARENT = frozenset(f"{W}{t}" for t in ("ins", "moveTo", "customXml", "smartTag"))
# ...and those whose content is not (as docx_rich.HIDDEN): a tracked deletion.
_DELETED = frozenset(f"{W}{t}" for t in ("del", "moveFrom"))
_INTEGRALS = set("∫∬∭∮∯∰")
_TOKENS = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]|\s+|.", re.UNICODE)


class UnsupportedMath(RuntimeError):
    """An equation holds a structure this converter cannot represent."""


def _val(parent: etree._Element | None, name: str, default: str | None = None) -> str | None:
    el = parent.find(f"{M}{name}") if parent is not None else None
    if el is None:
        return default
    return el.get(f"{M}val", "on")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _el(name: str, *children: str, **attrs: str) -> str:
    at = "".join(f' {k}="{_escape(v)}"' for k, v in attrs.items())
    return f"<{name}{at}>{''.join(children)}</{name}>"


def _mo(char: str, **attrs: str) -> str:
    return _el("mo", _escape(char), **attrs)


def _row(items: list[str]) -> str:
    return items[0] if len(items) == 1 else _el("mrow", *items)


class _Converter:
    def __init__(self, hidden: Callable[[etree._Element], bool]):
        self.hidden = hidden

    def children(self, el: etree._Element | None) -> list[str]:
        out: list[str] = []
        if el is None:
            return out
        for child in el:
            if (not isinstance(child.tag, str) or _SKIP.match(child.tag) or child.tag in _DELETED
                    or self.hidden(child)):
                continue
            if child.tag in _TRANSPARENT:
                out.extend(self.children(child))
            else:
                out.extend(self.node(child))
        return out

    def arg(self, el: etree._Element, name: str) -> str:
        """One argument (`m:e`, `m:num`, ...) as a single MathML child; an empty
        one is an empty `mrow`, which MathML allows in every argument slot."""
        items = self.children(el.find(f"{M}{name}"))
        return _el("mrow") if not items else _row(items)

    def run(self, r: etree._Element) -> list[str]:
        text = "".join(t.text or "" for t in r.iter(f"{M}t") if not self.hidden(t))
        if not text:
            return []
        rpr = r.find(f"{M}rPr")
        if _val(rpr, "nor") not in (None, "0", "off", "false"):
            return [_el("mtext", _escape(text))]
        style = _val(rpr, "sty", "i")
        variant = {"p": "normal", "b": "bold", "bi": "bold-italic"}.get(style)
        out = []
        for tok in _TOKENS.findall(text):
            if tok.isspace():
                continue                       # Word math ignores typed spaces
            if tok[0].isdigit():
                out.append(_el("mn", _escape(tok)))
            elif tok.isalpha():
                out.append(_el("mi", _escape(tok), **({"mathvariant": variant} if variant else {})))
            else:
                out.append(_mo(tok))
        return out

    def node(self, el: etree._Element) -> list[str]:
        if el.tag == f"{W}r":
            # Ordinary document text Word left inside the equation.
            text = "".join(t.text or "" for t in el.iter(f"{W}t") if not self.hidden(t))
            return [_el("mtext", _escape(text))] if text else []
        name = etree.QName(el).localname if el.tag.startswith(M) else None
        if name == "r":
            return self.run(el)
        handler = getattr(self, f"m_{name}", None) if name else None
        if handler is None:
            raise UnsupportedMath(f"the equation holds <{etree.QName(el).localname}>, which ingest cannot set yet")
        return [handler(el)]

    # -- structures -------------------------------------------------------------
    def m_f(self, el):
        kind = _val(el.find(f"{M}fPr"), "type", "bar")
        num, den = self.arg(el, "num"), self.arg(el, "den")
        if kind == "lin":
            return _el("mrow", num, _mo("/"), den)
        return _el("mfrac", num, den, **({"linethickness": "0"} if kind == "noBar" else {}))

    def m_sSup(self, el):
        return _el("msup", self.arg(el, "e"), self.arg(el, "sup"))

    def m_sSub(self, el):
        return _el("msub", self.arg(el, "e"), self.arg(el, "sub"))

    def m_sSubSup(self, el):
        return _el("msubsup", self.arg(el, "e"), self.arg(el, "sub"), self.arg(el, "sup"))

    def m_sPre(self, el):
        return _el("mmultiscripts", self.arg(el, "e"), _el("mprescripts"), self.arg(el, "sub"), self.arg(el, "sup"))

    def m_rad(self, el):
        hide = _val(el.find(f"{M}radPr"), "degHide") not in (None, "0", "off", "false")
        degree = self.children(el.find(f"{M}deg"))
        if hide or not degree:
            return _el("msqrt", self.arg(el, "e"))
        return _el("mroot", self.arg(el, "e"), _row(degree))

    def m_nary(self, el):
        pr = el.find(f"{M}naryPr")
        char = _val(pr, "chr", "∫")
        loc = _val(pr, "limLoc", "subSup" if char in _INTEGRALS else "undOvr")
        sub = None if _val(pr, "subHide") not in (None, "0", "off", "false") else self.arg(el, "sub")
        sup = None if _val(pr, "supHide") not in (None, "0", "off", "false") else self.arg(el, "sup")
        op = _mo(char, largeop="true", movablelimits="false")
        under, both, over = (("munder", "munderover", "mover") if loc == "undOvr"
                             else ("msub", "msubsup", "msup"))
        if sub and sup:
            op = _el(both, op, sub, sup)
        elif sub:
            op = _el(under, op, sub)
        elif sup:
            op = _el(over, op, sup)
        return _el("mrow", op, self.arg(el, "e"))

    def m_d(self, el):
        pr = el.find(f"{M}dPr")
        beg, end, sep = _val(pr, "begChr", "("), _val(pr, "endChr", ")"), _val(pr, "sepChr", "|")
        items: list[str] = [_mo(beg, fence="true")] if beg else []
        for i, e in enumerate(el.findall(f"{M}e")):
            if i and sep:
                items.append(_mo(sep, separator="true"))
            inner = self.children(e)
            items.append(_row(inner) if inner else _el("mrow"))
        if end:
            items.append(_mo(end, fence="true"))
        return _el("mrow", *items)

    def m_m(self, el):
        rows = [_el("mtr", *(_el("mtd", self._cell(e)) for e in mr.findall(f"{M}e")))
                for mr in el.findall(f"{M}mr")]
        return _el("mtable", *rows)

    def _cell(self, e):
        inner = self.children(e)
        return _row(inner) if inner else _el("mrow")

    def m_eqArr(self, el):
        return _el("mtable", *(_el("mtr", _el("mtd", self._cell(e))) for e in el.findall(f"{M}e")),
                   columnalign="left")

    def m_acc(self, el):
        char = _val(el.find(f"{M}accPr"), "chr", "̂")
        return _el("mover", self.arg(el, "e"), _mo(char), accent="true")

    def m_bar(self, el):
        top = _val(el.find(f"{M}barPr"), "pos", "bot") == "top"
        return (_el("mover", self.arg(el, "e"), _mo("¯"), accent="true") if top
                else _el("munder", self.arg(el, "e"), _mo("_"), accentunder="true"))

    def m_groupChr(self, el):
        pr = el.find(f"{M}groupChrPr")
        char, pos = _val(pr, "chr", "⏟"), _val(pr, "pos", "bot")
        return _el("mover" if pos == "top" else "munder", self.arg(el, "e"), _mo(char, stretchy="true"))

    def m_limLow(self, el):
        return _el("munder", self.arg(el, "e"), self.arg(el, "lim"))

    def m_limUpp(self, el):
        return _el("mover", self.arg(el, "e"), self.arg(el, "lim"))

    def m_func(self, el):
        # A function name is one identifier set upright ("sin", "log"), not a
        # row of italic letters.
        name = "".join(t.text or "" for t in el.find(f"{M}fName").iter(f"{M}t") if not self.hidden(t))
        nested = [c for c in el.find(f"{M}fName") if isinstance(c.tag, str) and c.tag != f"{M}r"
                  and not _SKIP.match(c.tag)]
        fname = self.arg(el, "fName") if nested else _el("mi", _escape(name.strip()), mathvariant="normal")
        return _el("mrow", fname, self.arg(el, "e"))

    def m_box(self, el):
        return self.arg(el, "e")

    def m_borderBox(self, el):
        return _el("menclose", self.arg(el, "e"), notation="box")

    def m_oMath(self, el):
        inner = self.children(el)
        return _row(inner) if inner else _el("mrow")


def to_mathml(o_math: etree._Element, hidden: Callable[[etree._Element], bool]) -> str:
    """`m:oMath` as one compact `<math>` element. `hidden` is ingest's own test
    for text that is in the XML but not the document (a tracked deletion)."""
    inner = _Converter(hidden).children(o_math)
    return f'<math xmlns="{MATHML_NS}">{"".join(inner)}</math>'


def omml_text(o_math: etree._Element, hidden: Callable[[etree._Element], bool]) -> str:
    """The equation's own characters (`m:t`, and `w:t` of a text run inside it),
    read off the XML for the no-loss check; shares nothing with the conversion
    above."""
    return "".join(t.text or "" for t in o_math.iter(f"{M}t", f"{W}t") if not hidden(t))


__all__ = ["UnsupportedMath", "omml_text", "to_mathml"]
