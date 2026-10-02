"""
The inside margin each page of a press PDF actually prints with (W6).

Read from the file, not from the design: poppler's `pdftotext -bbox` lists
every word's box, and the margin on the bound edge is the distance from the
trim line to the word nearest the spine. Page 1 is a recto, so odd pages bind
on the left and even pages on the right. A blank page has no margin to measure
and is left out.

Measured per page against the trim box, so bleed is excluded: pdftotext
reports coordinates from the media box's top-left corner.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from publisher_prepress.geometry import PT_PER_MM

PDFTOTEXT_TIMEOUT_S = 120

_PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.DOTALL)
_WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)"')


def inside_margins(bbox_html: str, trim_left_pt: float, trim_right_pt: float) -> dict[int, float]:
    """{page: inside margin in mm} from `pdftotext -bbox` output. The trim
    edges are x offsets (pt) from the media box's left edge."""
    margins = {}
    for number, page in enumerate(_PAGE_RE.finditer(bbox_html), start=1):
        words = [(float(a), float(b)) for a, b in _WORD_RE.findall(page.group(3))]
        if not words:
            continue
        gap = (min(x0 for x0, _ in words) - trim_left_pt if number % 2
               else trim_right_pt - max(x1 for _, x1 in words))
        margins[number] = round(gap / PT_PER_MM, 2)
    return margins


def measure_inside_margins(pdf_path: Path, media: tuple | None, trim: tuple | None) -> dict:
    """`{"margins": {page: mm}}`, or `{"unmeasured": why}`."""
    if not media or not trim:
        return {"unmeasured": "the PDF declares no MediaBox/TrimBox"}
    binary = shutil.which("pdftotext")
    if binary is None:
        return {"unmeasured": "pdftotext (poppler-utils) is not installed"}
    try:
        html = subprocess.run([binary, "-q", "-bbox", str(pdf_path), "-"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=PDFTOTEXT_TIMEOUT_S, check=True).stdout
    except (subprocess.SubprocessError, OSError) as error:
        return {"unmeasured": f"pdftotext failed: {str(error)[:300]}"}
    return {"margins": inside_margins(html, trim[0] - media[0], trim[2] - media[0])}


def required_inside_margin(bands: list[dict], page_count: int) -> float | None:
    """The profile's minimum inside margin (mm) for a book of `page_count`
    pages: the first band whose `maxPages` covers it, else the last band."""
    for band in bands:
        if page_count <= band["maxPages"]:
            return band["mm"]
    return bands[-1]["mm"] if bands else None
