"""publisher_structure.mathml: what an equation may hold, and its text."""

from __future__ import annotations

import pytest

from publisher_structure.mathml import MathMLError, math_text, parse, render

NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'
HALF = f'<math {NS}><mfrac><mi>a</mi><mn>2</mn></mfrac></math>'


def test_its_text_is_its_characters_in_order():
    assert math_text(HALF) == "a2"
    assert math_text(f'<math {NS}><mrow><mo>(</mo><mi>x</mi><mo>&lt;</mo><mn>1</mn><mo>)</mo></mrow></math>') == "(x<1)"


@pytest.mark.parametrize("bad", [
    '<math><mi>a</mi></math>',                                          # no namespace
    f'<mrow {NS}><mi>a</mi></mrow>',                                    # not <math>
    f'<math {NS}><mphantom><mi>a</mi></mphantom></math>',              # text never drawn
    f'<math {NS}><semantics><mi>a</mi><annotation>a</annotation></semantics></math>',
    f'<math {NS}><mi onclick="x()">a</mi></math>',                     # an event handler
    f'<math {NS}><mi alttext="a">a</mi></math>',                       # alttext off the root
    f'<math {NS}> <mi>a</mi></math>',                                   # whitespace between elements
    f'<math {NS}><mrow>a<mi>b</mi></mrow></math>',                      # mixed content
    f'<math {NS}><mi>f</mi><mo>⁡</mo><mi>x</mi></math>',           # invisible operator
    f'<!DOCTYPE math [<!ENTITY a "aa">]><math {NS}><mi>&a;</mi></math>',
    f'<math {NS}><mi>a</mi>',                                           # not well-formed
    f'<math {NS}><svg xmlns="http://www.w3.org/2000/svg"/></math>',     # another namespace
])
def test_anything_else_is_refused(bad):
    with pytest.raises(MathMLError):
        parse(bad)


def test_a_token_keeps_its_own_spaces():
    assert math_text(f'<math {NS}><mtext>if </mtext><mi>x</mi></math>') == "if x"


def test_render_reserializes_and_sets_the_display():
    block = render(HALF, display=True)
    assert block == f'<math {NS} display="block"><mfrac><mi>a</mi><mn>2</mn></mfrac></math>'
    inline = render(f'<math {NS} display="block"><mi>a</mi></math>', display=False)
    assert 'display' not in inline
    assert render(f'<math {NS}><mo>&lt;</mo></math>', display=False).endswith("<mo>&lt;</mo></math>")
    assert math_text(block) == math_text(HALF)
