"""
corpus/raster-diff/compare.py: the nightly raster-diff gate passes an
unchanged render and fails every way a book can change. The SSIM it computes
was checked against scikit-image's on real pages (0.547 vs 0.553 for a 1 px
shift); here only its behaviour is pinned, on drawn pages.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image, ImageDraw

_spec = importlib.util.spec_from_file_location(
    "compare", Path(__file__).resolve().parents[1] / "corpus/raster-diff/compare.py")
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


def _page(lines: int, shift: int = 0) -> Image.Image:
    page = Image.new("L", (200, 300), 255)
    draw = ImageDraw.Draw(page)
    for i in range(lines):
        draw.rectangle((20 + shift, 20 + 14 * i, 170 + shift, 28 + 14 * i), fill=0)
    return page


def _tree(root: Path, books: dict[str, list[Image.Image]]) -> Path:
    for name, pages in books.items():
        (root / name).mkdir(parents=True)
        for n, page in enumerate(pages, start=1):
            page.save(root / name / f"page-{n:04d}.png")
    return root


def test_an_unchanged_render_passes(tmp_path):
    book = {"novel": [_page(10), _page(12)]}
    report = compare.compare_trees(_tree(tmp_path / "a", book), _tree(tmp_path / "b", book))
    assert report["passed"] and report["books"][0]["minSsim"] == 1.0


def test_a_moved_line_a_lost_page_and_a_lost_book_each_fail(tmp_path):
    base = _tree(tmp_path / "a", {"novel": [_page(10), _page(12)], "memoir": [_page(5)]})
    moved = compare.compare_trees(base, _tree(tmp_path / "b", {"novel": [_page(10), _page(12, shift=3)],
                                                               "memoir": [_page(5)]}))
    assert not moved["passed"] and "page-0002.png: SSIM" in moved["books"][1]["failures"][0]
    shorter = compare.compare_trees(base, _tree(tmp_path / "c", {"novel": [_page(10)], "memoir": [_page(5)]}))
    assert not shorter["passed"] and "page count changed: 2 -> 1" in shorter["books"][1]["failures"]
    gone = compare.compare_trees(base, _tree(tmp_path / "d", {"novel": [_page(10), _page(12)]}))
    assert not gone["passed"] and gone["missing"] == ["memoir"]


def test_a_new_book_is_reported_not_compared_and_nothing_compared_is_not_a_pass(tmp_path):
    base = _tree(tmp_path / "a", {"novel": [_page(10)]})
    grown = compare.compare_trees(base, _tree(tmp_path / "b", {"novel": [_page(10)], "poems": [_page(3)]}))
    assert grown["passed"] and grown["unbaselined"] == ["poems"]
    empty = compare.compare_trees(tmp_path / "none", _tree(tmp_path / "c", {"poems": [_page(3)]}))
    assert not empty["passed"]
