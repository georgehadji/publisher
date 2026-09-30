"""Black and grey text print on the black plate alone.

weasyprint writes a CSS hex colour as RGB, and Ghostscript's press conversion
turned RGB black into C72 M67 Y67 K88: every line of the first real book was
rich black. rendering.black_plate sends neutral greys to K for both render paths.
"""
import pytest

from publisher_prepress.ghostscript import find_binary, to_pdfx
from publisher_prepress.preflight import measure_ink
from stages.rendering import black_plate, emit_css
from stages.typst_stages import _emit_typst


def test_black_plate_takes_neutral_greys_only():
    assert black_plate("#000000") == 1
    assert black_plate("#666666") == 0.6
    assert black_plate("#FFFFFF") == 0
    assert black_plate("#7A1F2B") is None   # a colour keeps its RGB


def test_both_render_paths_set_black_text_as_k():
    assert "color: device-cmyk(0 0 0 1);" in emit_css({})
    assert "fill: cmyk(0%, 0%, 0%, 100%))" in _emit_typst({})
    coloured = {"colors": {"text": "#7A1F2B"}}
    assert "color: #7A1F2B;" in emit_css(coloured)
    assert 'fill: rgb("#7A1F2B"))' in _emit_typst(coloured)


@pytest.mark.skipif(find_binary() is None, reason="needs Ghostscript")
def test_black_text_survives_the_press_conversion_as_k_only(tmp_path):
    weasyprint = pytest.importorskip("weasyprint")
    raw, press = tmp_path / "raw.pdf", tmp_path / "press.pdf"
    weasyprint.HTML(string=f"<style>{emit_css({})}</style><p>Black text.</p>").write_pdf(raw)
    to_pdfx(raw, press, tmp_path / "work")
    assert measure_ink(press)["rich_black_text_pages"] == []
