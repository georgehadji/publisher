"""Tests for geometry module."""

from publisher_prepress.geometry import (
    TrimSize, BleedBox, PageGeometry, spine_width, cover_dimensions,
    crop_marks, gutter_offset, mm_to_pt, pt_to_mm,
)


def test_trim_size():
    t = TrimSize(width=152.4, height=228.6)
    assert t.width == 152.4
    assert t.height == 228.6


def test_bleed_box_uniform():
    b = BleedBox.uniform(3.0)
    assert b.top == 3.0
    assert b.bottom == 3.0
    assert b.inside == 3.0
    assert b.outside == 3.0


def test_page_geometry():
    trim = TrimSize(152.4, 228.6)
    bleed = BleedBox.uniform(3.0)
    geo = PageGeometry(trim=trim, bleed=bleed)
    
    assert geo.media_width == 152.4 + 3.0 + 3.0  # 158.4
    assert geo.media_height == 228.6 + 3.0 + 3.0  # 234.6
    assert geo.trim_box == (3.0, 3.0, 155.4, 231.6)


def test_spine_width():
    s = spine_width(300)
    assert s == 18.0  # 300 * 0.06


def test_spine_width_thick_paper():
    s = spine_width(300, paper_basis=0.09)
    assert s == 27.0


def test_cover_dimensions():
    trim = TrimSize(152.4, 228.6)
    bleed = BleedBox.uniform(3.0)
    spine = spine_width(300)  # 18.0
    
    w, h = cover_dimensions(trim, spine, bleed)
    # (152.4 + 6) * 2 + 18 = 334.8
    assert w == pytest.approx(334.8, 0.1)
    # 228.6 + 6 = 234.6
    assert h == pytest.approx(234.6, 0.1)


def test_crop_marks():
    trim = TrimSize(152.4, 228.6)
    bleed = BleedBox.uniform(3.0)
    marks = crop_marks(trim, bleed)
    
    assert len(marks) == 8  # 4 corners x 2 lines each


def test_gutter_offset():
    recto = gutter_offset(1)
    assert recto == 5.0
    verso = gutter_offset(2)
    assert verso == 5.0


def test_mm_to_pt():
    pt = mm_to_pt(25.4)  # 1 inch
    assert pt == pytest.approx(72.0, 0.1)


def test_pt_to_mm():
    mm = pt_to_mm(72.0)
    assert mm == pytest.approx(25.4, 0.1)


# Allow pytest.approx
import pytest
