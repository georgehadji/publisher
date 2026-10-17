"""
An equation's text, read by every side of the integrity gate: `ast_text` (the
AST), `extract_text_from_html` over `extract`'s HTML (ast-assemble), and the
EPUB's spine text (the epub stage). They agree only through
`publisher_structure.mathml`; a fourth reading would be a drift.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from publisher_stages import StageCtx
from publisher_structure.mathml import MathMLError
from publisher_structure.rules import extract_text_from_html, normalize_text
from stages.rendering import ast_to_html
from stages.text_stream import ast_text

NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'
WAVE = (f'<math {NS}><mi>E</mi><mo>=</mo><mfrac><mn>1</mn><mn>8</mn></mfrac>'
        '<mi>ρ</mi><mi>g</mi><msup><mi>H</mi><mn>2</mn></msup></math>')
SUM = (f'<math {NS}><munderover><mo>∑</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>'
       '<mi>n</mi></munderover><msub><mi>x</mi><mi>i</mi></msub></math>')


def _book() -> dict:
    return {"schema": "doc-effective/1", "metadata": {"title": "T", "language": "en"},
            "frontMatter": [], "backMatter": [], "body": [
        {"type": "chapter", "attrs": {"number": 1, "id": "c1", "title": "Waves"}, "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "The energy grows as "},
                {"type": "math", "content": WAVE},
                {"type": "text", "text": ", and the sum"}]},
            {"type": "equation", "content": SUM},
            {"type": "paragraph", "content": [{"type": "text", "text": "closes the proof."}]}]}]}


def _ctx(tmp_path) -> StageCtx:
    return StageCtx(build_id="t", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                    memory_budget_mb=128, work_dir=str(tmp_path / "work"),
                    cas_root=str(tmp_path / "cas"))


def _doc(tmp_path, book: dict) -> str:
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(book), encoding="utf-8")
    return str(path)


def test_the_print_gate_reads_an_equation_as_the_ast_does():
    book = _book()
    source = normalize_text(ast_text(book))
    assert "grows as E=18ρgH2, and the sum ∑i=1nxi closes" in source
    assert normalize_text(extract_text_from_html(ast_to_html(book))) == source


def test_a_word_equation_passes_ingest_and_the_print_gate():
    from pathlib import Path
    from publisher_ingest import docx_to_ast
    word = Path(__file__).resolve().parents[2] / "corpus" / "word" / "word-equation.docx"
    book = docx_to_ast(word, store_media=lambda b, t, n: "0" * 64)
    source = normalize_text(ast_text(book))
    assert "square of its height: E=18ρgH2." in source
    assert normalize_text(extract_text_from_html(ast_to_html(book))) == source


def test_the_epub_stage_passes_its_own_text_check(tmp_path):
    from stages.secondary_output_stages import epub
    # The stage fails unless the stored package's text equals ast_text.
    epub(_ctx(tmp_path), doc_path=_doc(tmp_path, _book()))


def test_a_displayed_equation_is_a_block_and_an_inline_one_is_not():
    html = ast_to_html(_book())
    assert f'<div class="equation"><math {NS} display="block"><munderover>' in html
    assert f'grows as <math {NS}><mi>E</mi>' in html


def test_the_page_carries_parsed_mathml_not_the_stored_string():
    book = _book()
    book["body"][0]["content"][1]["content"] = SUM.replace("<mi>n</mi>", '<mi onclick="x()">n</mi>')
    with pytest.raises(MathMLError):
        ast_to_html(book)


TOOLS = bool(shutil.which("pandoc") and shutil.which("typst"))


@pytest.mark.skipif(not TOOLS, reason="pandoc or typst is not installed")
def test_the_css_path_draws_each_equation_in_black_ink_alone(tmp_path):
    """weasyprint has no MathML; Typst draws each equation, and its RGB black is
    rewritten to K, so the page holds no four-colour black anywhere."""
    pytest.importorskip("weasyprint")
    from publisher_prepress import ghostscript
    if ghostscript.find_binary() is None:
        pytest.skip("Ghostscript is not installed")
    from stages.paginate_stage import paginate

    ctx = _ctx(tmp_path)
    result = paginate(ctx, doc_path=_doc(tmp_path, _book()))
    svgs = sorted((tmp_path / "work" / "math").glob("*.svg"))
    assert len([s for s in svgs if not s.name.startswith("eq-")]) == 2
    assert all("device-cmyk(0 0 0 1)" in s.read_text(encoding="utf-8") and 'fill="#' not in s.read_text(encoding="utf-8")
               for s in svgs if not s.name.startswith("eq-"))

    pdf = next(a for a in result.artifacts if a.kind == "pdf")
    path = Path(ctx.cas_root) / pdf.hash[:2] / pdf.hash[2:4] / pdf.hash
    inks = set().union(*ghostscript.page_colours(path))
    assert (0, 0, 0, 100) in inks
    assert not [c for c in inks if c[3] > 50 and (c[0] or c[1] or c[2])], "rich black on the page"


def test_math_render_lowers_an_inline_equation_by_its_depth(tmp_path):
    from stages.math_render import typeset_equations
    if not (shutil.which("pandoc") and shutil.which("typst")):
        pytest.skip("pandoc or typst is not installed")
    from stages.typst_stages import _run
    html = typeset_equations(ast_to_html(_book()), tmp_path, pandoc=shutil.which("pandoc"),
                             typst=shutil.which("typst"), run=_run)
    assert "<math" not in html
    inline = re.search(r'<img class="math" src="math/[0-9a-f]{64}\.svg" style="height: ([\d.]+)em; '
                       r'vertical-align: -([\d.]+)em" alt=""/>', html)
    assert inline and 0 < float(inline.group(2)) < float(inline.group(1))
    assert re.search(r'<div class="equation"><img class="math-display" src="math/[0-9a-f]{64}\.svg" '
                     r'style="height: [\d.]+em" alt=""/></div>', html)


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc is not installed")
def test_the_idml_deliverable_refuses_equations_rather_than_flatten_them(tmp_path):
    from publisher_stages import StageError
    from stages.idml_stage import idml
    with pytest.raises(StageError) as refused:
        idml(_ctx(tmp_path), doc_path=_doc(tmp_path, _book()))
    assert "cannot carry equations" in refused.value.message


@pytest.mark.skipif(not TOOLS, reason="pandoc or typst is not installed")
def test_every_equation_word_writes_is_drawn(tmp_path):
    """The Word fixture's matrix, roots, n-ary sum and functions go through
    pandoc and Typst as the CSS path sends them; none is left as MathML."""
    from publisher_ingest import docx_to_ast
    from stages.math_render import typeset_equations
    from stages.typst_stages import _run
    word = Path(__file__).resolve().parents[2] / "corpus" / "word" / "word-equation.docx"
    html = ast_to_html(docx_to_ast(word, store_media=lambda b, t, n: "0" * 64))
    drawn = typeset_equations(html, tmp_path, pandoc=shutil.which("pandoc"),
                              typst=shutil.which("typst"), run=_run)
    assert "<math" not in drawn
    assert drawn.count('<img class="math-display"') == 5 and drawn.count('<img class="math"') == 2
