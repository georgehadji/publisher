"""
DesignSpec template presets.

From ARCHITECTURE.md §2.7 and BUILD_PLAN.md §3.7:
8 starter templates plus the tracer bullet default, each with:
- Trim size, margins, typography, grid
- Folio and running head policies
- Chapter opening treatments
- Font pairings (from the built-in OFL vault)
"""

# ── Literary Novel (6x9, EB Garamond) ───────────────────────────

LITERARY = {
    "schema": "designspec/1",
    "name": "Literary Novel",
    "preferredEngine": "typst",
    "trimSize": {"width": 139.7, "height": 215.9, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "EB Garamond"},
        "headingFont": {"family": "EB Garamond"},
        "bodySize": 10.5,
        "leading": 14.0,
        "scaleRatio": 1.25,
        "measure": 66,
        "bodyAlignment": "justified",
        "paragraphIndent": 1.5,
        "opticalMargins": True,
    },
    "grid": {"type": "single", "baselineIncrement": 14.0},
    "margins": {"top": 18, "bottom": 20, "inside": 15, "outside": 20},
    "folio": {
        "position": "bottom-center",
        "style": "arabic",
        "suppressOn": ["chapter-opening"],
        "startNumber": 1,
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "centered",
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": True,
        "dropCapLines": 3,
        "titleTreatment": "centered",
        "firstParagraphStyle": "no-indent",
    },
    "fonts": [
        {"family": "EB Garamond", "style": "regular", "source": "bundled_ofl"},
        {"family": "EB Garamond", "style": "italic", "source": "bundled_ofl"},
        {"family": "EB Garamond", "style": "bold", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#000000", "paper": "#FFFFFF"},
}

# ── Thriller (6x9, Source Serif Pro, tighter margins) ───────────

THRILLER = {
    "schema": "designspec/1",
    "name": "Thriller",
    "preferredEngine": "typst",
    "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "Source Serif Pro"},
        "headingFont": {"family": "Source Sans Pro"},
        "bodySize": 11.0,
        "leading": 14.5,
        "scaleRatio": 1.2,
        "measure": 68,
        "bodyAlignment": "justified",
        "paragraphIndent": 1.2,
        "opticalMargins": True,
    },
    "grid": {"type": "single", "baselineIncrement": 14.5},
    "margins": {"top": 15, "bottom": 18, "inside": 13, "outside": 18},
    "folio": {
        "position": "bottom-outside",
        "style": "arabic",
        "suppressOn": ["chapter-opening"],
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "outer-margin",
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": True,
        "dropCapLines": 2,
        "titleTreatment": "recto-only",
        "firstParagraphStyle": "small-caps",
    },
    "fonts": [
        {"family": "Source Serif Pro", "style": "regular", "source": "bundled_ofl"},
        {"family": "Source Serif Pro", "style": "bold", "source": "bundled_ofl"},
        {"family": "Source Sans Pro", "style": "regular", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#1a1a1a", "paper": "#FFFFFF"},
}

# ── Memoir (5.5x8.5, elegant, chapter ornaments) ────────────────

MEMOIR = {
    "schema": "designspec/1",
    "name": "Memoir",
    "preferredEngine": "typst",
    "trimSize": {"width": 139.7, "height": 215.9, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "Libertinus Serif"},
        "headingFont": {"family": "Libertinus Serif"},
        "bodySize": 10.0,
        "leading": 14.0,
        "scaleRatio": 1.3,
        "measure": 62,
        "bodyAlignment": "justified",
        "paragraphIndent": 1.5,
        "opticalMargins": True,
    },
    "grid": {"type": "single", "baselineIncrement": 14.0},
    "margins": {"top": 20, "bottom": 22, "inside": 16, "outside": 22},
    "folio": {
        "position": "bottom-center",
        "style": "roman-lower",
        "suppressOn": ["chapter-opening"],
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "centered",
        "suppressOn": ["chapter-opening"],
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": True,
        "dropCapLines": 3,
        "titleTreatment": "centered",
        "ornament": "fleuron",
        "firstParagraphStyle": "no-indent",
    },
    "ornaments": {
        "dinkus": "❦",
        "chapterOrnament": "❧",
    },
    "fonts": [
        {"family": "Libertinus Serif", "style": "regular", "source": "bundled_ofl"},
        {"family": "Libertinus Serif", "style": "italic", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#000000", "paper": "#FAF8F5"},
}

# ── Academic (6x9, serif body, sans headings, footnotes) ────────

ACADEMIC = {
    "schema": "designspec/1",
    "name": "Academic",
    "preferredEngine": "typst",
    "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "Noto Serif"},
        "headingFont": {"family": "Noto Sans"},
        "bodySize": 10.5,
        "leading": 14.5,
        "scaleRatio": 1.25,
        "measure": 66,
        "bodyAlignment": "justified",
        "paragraphIndent": 0,
        "paragraphSpacing": 0.5,
        "opticalMargins": False,
    },
    "grid": {"type": "single", "baselineIncrement": 14.5},
    "margins": {"top": 18, "bottom": 22, "inside": 18, "outside": 22},
    "folio": {
        "position": "bottom-center",
        "style": "arabic",
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "centered",
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": False,
        "titleTreatment": "centered",
        "firstParagraphStyle": "normal",
    },
    "fonts": [
        {"family": "Noto Serif", "style": "regular", "source": "bundled_ofl"},
        {"family": "Noto Serif", "style": "italic", "source": "bundled_ofl"},
        {"family": "Noto Sans", "style": "regular", "source": "bundled_ofl"},
        {"family": "Fira Mono", "style": "regular", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#000000", "paper": "#FFFFFF"},
}

# ── Poetry (5.5x8.5, generous margins, elegant) ─────────────────

POETRY = {
    "schema": "designspec/1",
    "name": "Poetry",
    "preferredEngine": "typst",
    "trimSize": {"width": 139.7, "height": 215.9, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "EB Garamond"},
        "headingFont": {"family": "EB Garamond"},
        "bodySize": 11.0,
        "leading": 16.0,
        "scaleRatio": 1.2,
        "measure": 55,
        "bodyAlignment": "ragged-right",
        "paragraphIndent": 0,
        "opticalMargins": True,
    },
    "grid": {"type": "single", "baselineIncrement": 16.0},
    "margins": {"top": 22, "bottom": 25, "inside": 20, "outside": 25},
    "folio": {
        "position": "bottom-center",
        "style": "roman-lower",
        "suppressOn": ["chapter-opening", "title-page"],
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "centered",
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": False,
        "titleTreatment": "centered",
        "firstParagraphStyle": "normal",
    },
    "fonts": [
        {"family": "EB Garamond", "style": "regular", "source": "bundled_ofl"},
        {"family": "EB Garamond", "style": "italic", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#000000", "paper": "#FFFFFF"},
}

# ── Sci-Fi / Fantasy (6x9, bold modern face) ────────────────────

SCIFI = {
    "schema": "designspec/1",
    "name": "Science Fiction",
    "preferredEngine": "typst",
    "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "Merriweather"},
        "headingFont": {"family": "Merriweather"},
        "bodySize": 10.5,
        "leading": 14.5,
        "scaleRatio": 1.3,
        "measure": 65,
        "bodyAlignment": "justified",
        "paragraphIndent": 1.5,
        "opticalMargins": True,
    },
    "grid": {"type": "single", "baselineIncrement": 14.5},
    "margins": {"top": 16, "bottom": 20, "inside": 15, "outside": 20},
    "folio": {
        "position": "bottom-center",
        "style": "arabic",
        "suppressOn": ["chapter-opening"],
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "centered",
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": True,
        "dropCapLines": 3,
        "titleTreatment": "centered",
        "firstParagraphStyle": "no-indent",
    },
    "fonts": [
        {"family": "Merriweather", "style": "regular", "source": "bundled_ofl"},
        {"family": "Merriweather", "style": "italic", "source": "bundled_ofl"},
        {"family": "Merriweather", "style": "bold", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#000000", "paper": "#FFFFFF"},
}

# ── Children's / Middle Grade (7x10, large type) ────────────────

CHILDRENS = {
    "schema": "designspec/1",
    "name": "Children's",
    "preferredEngine": "typst",
    "trimSize": {"width": 177.8, "height": 254.0, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "Source Serif Pro"},
        "headingFont": {"family": "Source Sans Pro"},
        "bodySize": 13.0,
        "leading": 18.0,
        "scaleRatio": 1.2,
        "measure": 55,
        "bodyAlignment": "ragged-right",
        "paragraphIndent": 0,
        "paragraphSpacing": 0.5,
        "opticalMargins": True,
    },
    "grid": {"type": "single", "baselineIncrement": 18.0},
    "margins": {"top": 20, "bottom": 22, "inside": 18, "outside": 22},
    "folio": {
        "position": "bottom-center",
        "style": "arabic",
        "suppressOn": ["chapter-opening"],
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "centered",
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": True,
        "dropCapLines": 2,
        "titleTreatment": "centered",
        "firstParagraphStyle": "no-indent",
    },
    "fonts": [
        {"family": "Source Serif Pro", "style": "regular", "source": "bundled_ofl"},
        {"family": "Source Sans Pro", "style": "regular", "source": "bundled_ofl"},
        {"family": "Source Sans Pro", "style": "bold", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#000000", "paper": "#FFFFFF"},
}

# ── Reference / Nonfiction (7.5x9.25, two-column, compact) ──────

REFERENCE = {
    "schema": "designspec/1",
    "name": "Reference",
    "preferredEngine": "typst",
    "trimSize": {"width": 190.5, "height": 235.0, "unit": "mm"},
    "typography": {
        "bodyFont": {"family": "Noto Serif"},
        "headingFont": {"family": "Noto Sans"},
        "bodySize": 9.5,
        "leading": 13.0,
        "scaleRatio": 1.2,
        "measure": 72,
        "bodyAlignment": "justified",
        "paragraphIndent": 0,
        "paragraphSpacing": 0.3,
        "opticalMargins": False,
    },
    "grid": {"type": "double", "baselineIncrement": 13.0},
    "margins": {"top": 15, "bottom": 18, "inside": 14, "outside": 16, "gutter": 8},
    "folio": {
        "position": "top-outside",
        "style": "arabic",
    },
    "runningHeads": {
        "rectoSource": "chapter-title",
        "versoSource": "book-title",
        "style": "centered",
    },
    "chapterOpenings": {
        "startsOn": "recto",
        "dropCap": False,
        "titleTreatment": "centered",
        "firstParagraphStyle": "normal",
    },
    "fonts": [
        {"family": "Noto Serif", "style": "regular", "source": "bundled_ofl"},
        {"family": "Noto Sans", "style": "regular", "source": "bundled_ofl"},
        {"family": "Noto Sans", "style": "bold", "source": "bundled_ofl"},
        {"family": "Fira Mono", "style": "regular", "source": "bundled_ofl"},
    ],
    "colors": {"text": "#000000", "paper": "#FFFFFF"},
}

# ── Registry ─────────────────────────────────────────────────────

TEMPLATES = {
    "literary": LITERARY,
    "thriller": THRILLER,
    "memoir": MEMOIR,
    "academic": ACADEMIC,
    "poetry": POETRY,
    "scifi": SCIFI,
    "childrens": CHILDRENS,
    "reference": REFERENCE,
}


def get_template(name: str) -> dict | None:
    """Get a template by name."""
    return TEMPLATES.get(name)


def list_templates() -> list[dict]:
    """List all available templates with metadata."""
    return [
        {
            "id": tid,
            "name": t["name"],
            "trimSize": t["trimSize"],
            "bodyFont": t["typography"]["bodyFont"]["family"],
            "preferredEngine": t["preferredEngine"],
        }
        for tid, t in TEMPLATES.items()
    ]
