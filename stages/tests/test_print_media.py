"""
Figures reach the print renderers without transparency (stages/media.py).

One figure with an alpha channel took the first real book's text with it: its
soft mask sits in the resources weasyprint shares across every page, PDF/X-1a
(PDF 1.3) cannot carry transparency, and Ghostscript rendered all 805 pages as
pictures. Print has no transparency to lose, so figures are composited onto
white before rendering.
"""

from __future__ import annotations

import io

from PIL import Image

from stages.media import opaque


def _encode(image: Image.Image, fmt: str, **kwargs) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_an_image_with_alpha_is_composited_onto_white():
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((1, 0), (255, 0, 0, 255))
    result = _open(opaque(_encode(image, "PNG")))
    assert result.format == "PNG" and result.mode == "RGB"
    assert result.getpixel((0, 0)) == (255, 255, 255)   # transparent -> paper
    assert result.getpixel((1, 0)) == (255, 0, 0)       # opaque -> unchanged


def test_an_opaque_image_is_left_byte_for_byte():
    data = _encode(Image.new("RGB", (3, 3), "navy"), "PNG")
    assert opaque(data) == data
    jpeg = _encode(Image.new("RGB", (3, 3), "navy"), "JPEG")
    assert opaque(jpeg) == jpeg


def test_a_palette_gif_with_a_transparent_colour_stays_a_gif():
    image = Image.new("P", (2, 1))
    image.putpalette([255, 255, 255, 0, 0, 0] + [0] * 762)
    image.putpixel((1, 0), 1)
    result = _open(opaque(_encode(image, "GIF", transparency=0)))
    assert result.format == "GIF"
    assert "transparency" not in result.info


def test_grey_with_alpha_stays_grey():
    result = _open(opaque(_encode(Image.new("LA", (1, 1), (0, 0)), "PNG")))
    assert result.mode == "L" and result.getpixel((0, 0)) == 255


def test_what_pillow_cannot_open_passes_through():
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    assert opaque(svg) == svg
