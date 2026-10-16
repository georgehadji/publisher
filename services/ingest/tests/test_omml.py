"""publisher_ingest.omml: Word's equations as the MathML the AST carries."""

from __future__ import annotations

from collections import Counter

import pytest
from lxml import etree

from publisher_ingest.docx_rich import UnsupportedContent, _hidden, equation_mathml
from publisher_ingest.omml import UnsupportedMath, omml_text, to_mathml
from publisher_structure.mathml import math_text, parse

NS = ('xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
      'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"')


def _r(text: str, sty: str | None = None) -> str:
    pr = f'<m:rPr><m:sty m:val="{sty}"/></m:rPr>' if sty else ""
    return f"<m:r>{pr}<m:t>{text}</m:t></m:r>"


def _math(body: str) -> etree._Element:
    return etree.fromstring(f"<m:oMath {NS}>{body}</m:oMath>")


def _convert(body: str) -> str:
    mathml = to_mathml(_math(body), _hidden)
    parse(mathml)                                   # the AST's own rules accept it
    return mathml.split(">", 1)[1].rsplit("</math>", 1)[0]


CASES = {
    "fraction": (f"<m:f><m:num>{_r('a')}</m:num><m:den>{_r('2')}</m:den></m:f>",
                 "<mfrac><mi>a</mi><mn>2</mn></mfrac>"),
    "linear fraction": (f'<m:f><m:fPr><m:type m:val="lin"/></m:fPr><m:num>{_r("a")}</m:num><m:den>{_r("b")}</m:den></m:f>',
                        "<mrow><mi>a</mi><mo>/</mo><mi>b</mi></mrow>"),
    "power": (f"<m:sSup><m:e>{_r('x')}</m:e><m:sup>{_r('2')}</m:sup></m:sSup>",
              "<msup><mi>x</mi><mn>2</mn></msup>"),
    "subscript and superscript": (f"<m:sSubSup><m:e>{_r('x')}</m:e><m:sub>{_r('i')}</m:sub><m:sup>{_r('2')}</m:sup></m:sSubSup>",
                                  "<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>"),
    "square root": (f'<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e>{_r("x")}</m:e></m:rad>',
                    "<msqrt><mi>x</mi></msqrt>"),
    "cube root": (f"<m:rad><m:deg>{_r('3')}</m:deg><m:e>{_r('x')}</m:e></m:rad>",
                  "<mroot><mi>x</mi><mn>3</mn></mroot>"),
    "sum": (f'<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr><m:sub>{_r("i=1")}</m:sub><m:sup>{_r("n")}</m:sup><m:e>{_r("i")}</m:e></m:nary>',
            '<mrow><munderover><mo largeop="true" movablelimits="false">∑</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>'
            "<mi>n</mi></munderover><mi>i</mi></mrow>"),
    "integral (Word's default operator)": (f"<m:nary><m:sub>{_r('0')}</m:sub><m:sup>{_r('1')}</m:sup><m:e>{_r('x')}</m:e></m:nary>",
                                           '<mrow><msubsup><mo largeop="true" movablelimits="false">∫</mo><mn>0</mn><mn>1</mn>'
                                           "</msubsup><mi>x</mi></mrow>"),
    "brackets (default parentheses)": (f"<m:d><m:e>{_r('a')}</m:e><m:e>{_r('b')}</m:e></m:d>",
                                       '<mrow><mo fence="true">(</mo><mi>a</mi><mo separator="true">|</mo><mi>b</mi><mo fence="true">)</mo></mrow>'),
    "set braces": (f'<m:d><m:dPr><m:begChr m:val="{{"/><m:endChr m:val="}}"/></m:dPr><m:e>{_r("x")}</m:e></m:d>',
                   '<mrow><mo fence="true">{</mo><mi>x</mi><mo fence="true">}</mo></mrow>'),
    "matrix": (f"<m:m><m:mr><m:e>{_r('1')}</m:e><m:e>{_r('0')}</m:e></m:mr><m:mr><m:e>{_r('0')}</m:e><m:e>{_r('1')}</m:e></m:mr></m:m>",
               "<mtable><mtr><mtd><mn>1</mn></mtd><mtd><mn>0</mn></mtd></mtr><mtr><mtd><mn>0</mn></mtd><mtd><mn>1</mn></mtd></mtr></mtable>"),
    "accent": (f'<m:acc><m:accPr><m:chr m:val="̇"/></m:accPr><m:e>{_r("x")}</m:e></m:acc>',
               '<mover accent="true"><mi>x</mi><mo>̇</mo></mover>'),
    "function": (f"<m:func><m:fName>{_r('sin', 'p')}</m:fName><m:e>{_r('θ')}</m:e></m:func>",
                 '<mrow><mi mathvariant="normal">sin</mi><mi>θ</mi></mrow>'),
    "limit": (f"<m:limLow><m:e>{_r('lim', 'p')}</m:e><m:lim>{_r('n→∞')}</m:lim></m:limLow>",
              '<munder><mrow><mi mathvariant="normal">l</mi><mi mathvariant="normal">i</mi><mi mathvariant="normal">m</mi></mrow>'
              "<mrow><mi>n</mi><mo>→</mo><mo>∞</mo></mrow></munder>"),
    "Greek and a decimal": (_r("π≈3.14"), "<mi>π</mi><mo>≈</mo><mn>3.14</mn>"),
    "ordinary text": ('<m:r><m:rPr><m:nor/></m:rPr><m:t>if </m:t></m:r>' + _r("x"), "<mtext>if </mtext><mi>x</mi>"),
}


@pytest.mark.parametrize("name", CASES)
def test_each_structure_converts(name):
    omml, mathml = CASES[name]
    assert _convert(omml) == mathml


@pytest.mark.parametrize("name", CASES)
def test_every_character_of_the_equation_survives(name):
    o_math = _math(CASES[name][0])
    want = Counter(c for c in omml_text(o_math, _hidden) if not c.isspace())
    assert not want - Counter(math_text(equation_mathml(o_math)))


def test_a_lost_character_fails_the_conversion(monkeypatch):
    from publisher_ingest import docx_rich
    monkeypatch.setattr(docx_rich, "to_mathml", lambda o, h: '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>a</mi></math>')
    with pytest.raises(UnsupportedContent, match="lost '2'"):
        docx_rich.equation_mathml(_math(CASES["fraction"][0]))


def test_a_tracked_deletion_inside_an_equation_is_not_text():
    o_math = _math(f'{_r("a")}<w:del w:id="1" w:author="x">{_r("b")}</w:del><w:ins w:id="2" w:author="x">{_r("c")}</w:ins>')
    assert math_text(to_mathml(o_math, _hidden)) == "ac"
    assert omml_text(o_math, _hidden) == "ac"


def test_what_it_does_not_know_is_refused_by_name():
    with pytest.raises(UnsupportedMath, match="phant"):
        to_mathml(_math(f"<m:phant><m:e>{_r('x')}</m:e></m:phant>"), _hidden)
    with pytest.raises(UnsupportedContent, match="phant"):
        equation_mathml(_math(f"<m:phant><m:e>{_r('x')}</m:e></m:phant>"))
