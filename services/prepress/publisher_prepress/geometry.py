"""
Geometry module — book geometry calculations.

From BUILD_PLAN.md §3.10 and ARCHITECTURE.md §2.6:
Calculates bleed, trim, marks, gutter curve, and spine width.
Every calculation is pure and deterministic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TrimSize:
    """Trim size in mm."""
    width: float
    height: float


@dataclass(frozen=True)
class BleedBox:
    """Bleed dimensions in mm."""
    top: float
    bottom: float
    inside: float
    outside: float

    @classmethod
    def uniform(cls, all_mm: float) -> "BleedBox":
        return cls(top=all_mm, bottom=all_mm, inside=all_mm, outside=all_mm)


@dataclass(frozen=True)
class PageGeometry:
    """Full geometry specification for a page."""
    trim: TrimSize
    bleed: BleedBox
    
    # Derived
    @property
    def media_width(self) -> float:
        """Width including bleed."""
        return self.trim.width + self.bleed.inside + self.bleed.outside
    
    @property
    def media_height(self) -> float:
        """Height including bleed."""
        return self.trim.height + self.bleed.top + self.bleed.bottom
    
    @property
    def trim_box(self) -> tuple[float, float, float, float]:
        """Trim box (left, bottom, right, top) relative to media."""
        return (
            self.bleed.inside,
            self.bleed.bottom,
            self.bleed.inside + self.trim.width,
            self.bleed.bottom + self.trim.height,
        )
    
    @property
    def bleed_box(self) -> tuple[float, float, float, float]:
        """Bleed box (left, bottom, right, top) — same as media."""
        return (0, 0, self.media_width, self.media_height)


def spine_width(page_count: int, paper_basis: float = 0.06) -> float:
    """
    Calculate spine width in mm.
    
    Paper basis is mm per page. Default 0.06 mm/page is typical for
    60# cream/white paper. For thicker paper (glossy art), use ~0.09.
    
    From KDP spec: spine = page_count × paper_thickness
    """
    return round(page_count * paper_basis, 2)


def cover_dimensions(
    trim: TrimSize,
    spine_mm: float,
    bleed: BleedBox,
) -> tuple[float, float]:
    """
    Calculate cover dimensions (width × height) including bleed and spine.
    
    Cover width = (trim.width + bleed.inside + bleed.outside) × 2 + spine
    Cover height = trim.height + bleed.top + bleed.bottom
    """
    cover_w = (trim.width + bleed.inside + bleed.outside) * 2 + spine_mm
    cover_h = trim.height + bleed.top + bleed.bottom
    return (cover_w, cover_h)


def crop_marks(trim: TrimSize, bleed: BleedBox, length: float = 5.0, offset: float = 3.0) -> list[dict]:
    """
    Generate crop mark positions as line segments.
    
    Each mark: {"x1": mm, "y1": mm, "x2": mm, "y2": mm}
    Origin is bottom-left of the media box.
    """
    marks = []
    mw = trim.width + bleed.inside + bleed.outside
    mh = trim.height + bleed.top + bleed.bottom
    
    tx = bleed.inside  # trim box left
    ty = bleed.bottom  # trim box bottom
    tw = trim.width
    th = trim.height
    
    # Corner marks — short lines extending beyond trim box
    # Bottom-left corner
    marks.append({"x1": tx - offset, "y1": ty - offset - length, "x2": tx - offset, "y2": ty - offset})
    marks.append({"x1": tx - offset - length, "y1": ty - offset, "x2": tx - offset, "y2": ty - offset})
    
    # Bottom-right corner
    marks.append({"x1": tx + tw + offset, "y1": ty - offset - length, "x2": tx + tw + offset, "y2": ty - offset})
    marks.append({"x1": tx + tw + offset, "y1": ty - offset, "x2": tx + tw + offset + length, "y2": ty - offset})
    
    # Top-left corner
    marks.append({"x1": tx - offset, "y1": ty + th + offset, "x2": tx - offset, "y2": ty + th + offset + length})
    marks.append({"x1": tx - offset - length, "y1": ty + th + offset, "x2": tx - offset, "y2": ty + th + offset})
    
    # Top-right corner
    marks.append({"x1": tx + tw + offset, "y1": ty + th + offset, "x2": tx + tw + offset, "y2": ty + th + offset + length})
    marks.append({"x1": tx + tw + offset, "y1": ty + th + offset, "x2": tx + tw + offset + length, "y2": ty + th + offset})
    
    return marks


def gutter_offset(page_number: int, binding_margin_mm: float = 5.0) -> float:
    """
    Calculate gutter offset for a given page number.
    
    Recto (odd) pages get gutter on the left.
    Verso (even) pages get gutter on the right.
    
    Gutter is the extra margin added to the binding side for thick books.
    Typical values: 0 for < 300 pages, 2-5 mm for thicker books.
    """
    return binding_margin_mm


def mm_to_pt(mm: float) -> float:
    """Convert millimetres to points (1 pt = 0.352778 mm)."""
    return mm / 0.352778


def pt_to_mm(pt: float) -> float:
    """Convert points to millimetres."""
    return pt * 0.352778


def pt_to_in(pt: float) -> float:
    """Convert points to inches."""
    return pt / 72.0


def mm_to_in(mm: float) -> float:
    """Convert millimetres to inches."""
    return mm / 25.4
