"""
Hyphen stacks and rivers, measured from the press PDF's word boxes (§2.1).

REMAINING_WORK.md §2.1 forbids finding these from pagemap hints: layout
metadata cannot say where a gap fell, so a detector built on it reports
"clean" where the truth is unknown. These are read from the file instead.
poppler's `pdftotext -bbox-layout` gives every word's box, grouped into the
lines and blocks poppler found, so a line's last word and the gaps between
its words are measured, not inferred.

- A hyphen stack is more than MAX_HYPHEN_RUN consecutive lines of one block
  ending in a hyphen.
- A river is a chain of wide inter-word gaps (RIVER_WIDE times the block's
  median gap) that overlap line to line through RIVER_MIN_LINES lines.
"""

from __future__ import annotations

import html
import re
import shutil
import statistics
import subprocess
from pathlib import Path

from publisher_prepress.margins import PDFTOTEXT_TIMEOUT_S

# ponytail: house thresholds, not a vendor's. A profile field when a vendor states one.
MAX_HYPHEN_RUN = 2          # three hyphenated line ends in a row is a stack
RIVER_MIN_LINES = 4         # a gap aligned through four lines reads as a channel
RIVER_WIDE = 1.5            # wide = this many times the block's median word gap
RIVER_OVERLAP_PT = 1.0      # gaps on adjacent lines must overlap by this much

HYPHENS = ("-", "‐", "­")   # hyphen-minus, hyphen, soft hyphen

BASELINE_PT = 1.0          # poppler <line>s this close in yMin are one printed line

_PAGE = re.compile(r"<page [^>]*>(.*?)</page>", re.DOTALL)
_BLOCK = re.compile(r"<block [^>]*>(.*?)</block>", re.DOTALL)
_LINE = re.compile(r'<line xMin="[\d.]+" yMin="([\d.]+)"[^>]*>(.*?)</line>', re.DOTALL)
_WORD = re.compile(r'<word xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>',
                   re.DOTALL)

Line = list[tuple[float, float, str]]   # (xMin, xMax, text) per word, left to right


def _blocks(page: str) -> list[list[Line]]:
    """Each block's printed lines. poppler cuts a printed line in two at a wide
    gap (measured: a 3-space gap in monospace became two <line>s on one
    baseline), and a wide gap is what a river is made of -- so the pieces are
    joined back by baseline before any gap is read."""
    blocks = []
    for block in _BLOCK.findall(page):
        lines: list[tuple[float, Line]] = []
        for y, inner in _LINE.findall(block):
            words = [(float(x0), float(x1), html.unescape(text)) for x0, x1, text in _WORD.findall(inner)]
            if lines and abs(lines[-1][0] - float(y)) <= BASELINE_PT:
                lines[-1] = (lines[-1][0], sorted(lines[-1][1] + words))
            else:
                lines.append((float(y), words))
        blocks.append([words for _, words in lines])
    return blocks


def hyphen_stacks(lines: list[Line]) -> int:
    """Runs of more than MAX_HYPHEN_RUN consecutive lines ending in a hyphen."""
    stacks = run = 0
    for line in lines:
        run = run + 1 if line and line[-1][2].endswith(HYPHENS) else 0
        stacks += run == MAX_HYPHEN_RUN + 1
    return stacks


def rivers(lines: list[Line]) -> int:
    """Chains of wide gaps overlapping line to line through RIVER_MIN_LINES lines."""
    gaps = [[(a[1], b[0]) for a, b in zip(line, line[1:]) if b[0] > a[1]] for line in lines]
    widths = [right - left for line in gaps for left, right in line]
    if not widths:
        return 0
    wide = RIVER_WIDE * statistics.median(widths)
    found = 0
    chains: list[tuple[float, float, int]] = []   # the previous line's wide gaps, with chain length
    for line in gaps:
        current = []
        for left, right in line:
            if right - left < wide:
                continue
            length = 1 + max((n for l, r, n in chains if min(r, right) - max(l, left) >= RIVER_OVERLAP_PT),
                             default=0)
            found += length == RIVER_MIN_LINES
            current.append((left, right, length))
        chains = current
    return found


def scan_spacing(bbox_layout: str) -> dict:
    """`{"pages": n, "hyphenStacks": [page, ...], "rivers": [page, ...]}` from
    `pdftotext -bbox-layout` output; a page is listed once per defect found."""
    found: dict = {"pages": 0, "hyphenStacks": [], "rivers": []}
    for number, page in enumerate(_PAGE.findall(bbox_layout), start=1):
        found["pages"] = number
        for block in _blocks(page):
            found["hyphenStacks"] += [number] * hyphen_stacks(block)
            found["rivers"] += [number] * rivers(block)
    return found


def measure_spacing(pdf_path: Path) -> dict:
    """`{"scan": scan_spacing(...)}`, or `{"unmeasured": why}`."""
    binary = shutil.which("pdftotext")
    if binary is None:
        return {"unmeasured": "pdftotext (poppler-utils) is not installed"}
    try:
        out = subprocess.run([binary, "-q", "-bbox-layout", str(pdf_path), "-"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=PDFTOTEXT_TIMEOUT_S, check=True).stdout
    except (subprocess.SubprocessError, OSError) as error:
        # xpdf's pdftotext (MiKTeX, Git for Windows) has no -bbox-layout and exits 99.
        return {"unmeasured": f"pdftotext -bbox-layout failed (poppler's is required): {str(error)[:300]}"}
    return {"scan": scan_spacing(out)}
