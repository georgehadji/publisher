#!/usr/bin/env python3
"""
Raster-diff regression gate (BUILD_PLAN.md §0: page-raster SSIM >= 0.995).

Compares two trees of page rasters, one directory per book of
`page-NNNN.png` (what `tracer_bullet.py --rasters` writes): a baseline and a
fresh render. A book fails when its page count changed, a page is missing, or
any page's SSIM falls under the threshold. A book only the new render has is
reported, not compared; one only the baseline has fails.

SSIM is computed here, in plain Python over Pillow's pixels (8x8 tiles, the
standard constants), because the version this replaced fell back to a
file-size ratio when scikit-image was missing and passed anything of a
similar size.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image   # a hard dependency: no image library, no comparison

THRESHOLD = 0.995
BLOCK = 8
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


def ssim(a: Image.Image, b: Image.Image) -> float:
    """Mean SSIM over BLOCK x BLOCK tiles of two grayscale images; 0.0 when
    their sizes differ (a page that changed size has changed)."""
    a, b = a.convert("L"), b.convert("L")
    if a.size != b.size:
        return 0.0
    width, height = a.size
    pa, pb = a.tobytes(), b.tobytes()
    total = tiles = 0
    n = BLOCK * BLOCK
    for top in range(0, height - BLOCK + 1, BLOCK):
        for left in range(0, width - BLOCK + 1, BLOCK):
            sa = sb = saa = sbb = sab = 0
            for row in range(top, top + BLOCK):
                start = row * width + left
                for x, y in zip(pa[start:start + BLOCK], pb[start:start + BLOCK]):
                    sa += x; sb += y; saa += x * x; sbb += y * y; sab += x * y
            ma, mb = sa / n, sb / n
            va, vb, cov = saa / n - ma * ma, sbb / n - mb * mb, sab / n - ma * mb
            total += ((2 * ma * mb + _C1) * (2 * cov + _C2)) / ((ma * ma + mb * mb + _C1) * (va + vb + _C2))
            tiles += 1
    return total / tiles if tiles else float(pa == pb)


def compare_book(baseline: Path, rendered: Path, threshold: float = THRESHOLD) -> dict:
    """{"book", "pages", "failures": [...], "minSsim"} for one book's two directories."""
    old, new = sorted(baseline.glob("page-*.png")), sorted(rendered.glob("page-*.png"))
    failures, scores = [], {}
    if len(old) != len(new):
        failures.append(f"page count changed: {len(old)} -> {len(new)}")
    for before in old:
        after = rendered / before.name
        if not after.exists():
            failures.append(f"{before.name}: not rendered")
            continue
        with Image.open(before) as x, Image.open(after) as y:
            scores[before.name] = ssim(x, y)
        if scores[before.name] < threshold:
            failures.append(f"{before.name}: SSIM {scores[before.name]:.4f} < {threshold}")
    return {"book": baseline.name, "pages": len(new), "failures": failures,
            "minSsim": min(scores.values(), default=None)}


def compare_trees(baseline: Path, rendered: Path, threshold: float = THRESHOLD) -> dict:
    books_old = {p.name for p in baseline.iterdir() if p.is_dir()} if baseline.is_dir() else set()
    books_new = {p.name for p in rendered.iterdir() if p.is_dir()}
    results = [compare_book(baseline / name, rendered / name, threshold)
               for name in sorted(books_old & books_new)]
    return {"books": results,
            "unbaselined": sorted(books_new - books_old),   # new books: nothing to compare yet
            "missing": sorted(books_old - books_new),       # a book that no longer renders
            "passed": bool(results) and not books_old - books_new
                      and not any(r["failures"] for r in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Raster-diff regression gate")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("rendered", type=Path)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    args = parser.parse_args()
    report = compare_trees(args.baseline, args.rendered, args.threshold)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
