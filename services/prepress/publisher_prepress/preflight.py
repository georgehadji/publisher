"""
Preflight rule engine — hard gate between build and delivery.

Every check maps to a vendor profile requirement.
Checks produce PreflightCheck results aggregated into a PreflightReport.
A failing check of severity=error blocks delivery.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from publisher_prepress.geometry import PT_PER_MM
from publisher_prepress.ghostscript import INK_TIMEOUT_S, GhostscriptError, find_binary, page_colours
from publisher_prepress.margins import PDFTOTEXT_TIMEOUT_S, measure_inside_margins, required_inside_margin

# pdfimages lists image placements without decoding them: seconds, even for a
# large book. The deadline is for a hung process, not a slow one.
PDFIMAGES_TIMEOUT_S = 120

# run_preflight renders the pages twice (measure_ink) and lists the images once
# (measure_image_ppi); the rest is byte scanning.
PREFLIGHT_TIMEOUT_S = 2 * INK_TIMEOUT_S + PDFIMAGES_TIMEOUT_S + PDFTOTEXT_TIMEOUT_S + 300


@dataclass
class PreflightCheck:
    """A single preflight check result."""
    code: str
    status: str  # "pass" | "fail" | "warn" | "skip"
    severity: str  # "error" | "warning" | "info"
    humanMessage: str
    suggestedFix: Optional[str] = None
    sourceRef: Optional[str] = None
    value: Optional[Any] = None
    expected: Optional[Any] = None
    policyUrl: Optional[str] = None


@dataclass
class PreflightReport:
    """Aggregate preflight result."""
    schema: str = "preflight/1"
    status: str = "pass"  # "pass" | "fail" | "warn"
    profileId: Optional[str] = None
    pageCount: Optional[int] = None
    checks: list[PreflightCheck] = field(default_factory=list)
    summary: dict = field(default_factory=lambda: {"passed": 0, "failed": 0, "warnings": 0, "policyViolations": 0})
    profileVersion: Optional[str] = None
    createdAt: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "status": self.status,
            "profileId": self.profileId,
            "pageCount": self.pageCount,
            "checks": [
                {"code": c.code, "status": c.status, "severity": c.severity,
                 "humanMessage": c.humanMessage, "suggestedFix": c.suggestedFix,
                 "sourceRef": c.sourceRef, "value": c.value, "expected": c.expected,
                 "policyUrl": c.policyUrl}
                for c in self.checks
            ],
            "summary": self.summary,
            "profileVersion": self.profileVersion,
            # No wall-clock fallback. This dict is serialized and written to the CAS by
            # stages/prepress_stages.py, so a `datetime.now()` here gives a
            # byte-identical PDF a DIFFERENT report hash on every run — defeating the
            # cache and the nightly byte-equality rebuild (ARCHITECTURE.md §2.5, D5:
            # "No wall-clock ... inside stage logic"). The caller supplies a timestamp
            # derived from the cache key; absent that, the field is simply omitted
            # rather than fabricated.
            "createdAt": self.createdAt,
        }


def _pass(code: str, humanMessage: str = "") -> PreflightCheck:
    return PreflightCheck(code=code, status="pass", severity="info",
                          humanMessage=humanMessage or f"{code}: passed")


def _fail(code: str, humanMessage: str, suggestedFix: str = "",
          value=None, expected=None, policyUrl: str = "") -> PreflightCheck:
    return PreflightCheck(code=code, status="fail", severity="error",
                          humanMessage=humanMessage, suggestedFix=suggestedFix,
                          value=value, expected=expected, policyUrl=policyUrl)


def _warn(code: str, humanMessage: str, suggestedFix: str = "",
          value=None, expected=None) -> PreflightCheck:
    return PreflightCheck(code=code, status="warn", severity="warning",
                          humanMessage=humanMessage, suggestedFix=suggestedFix,
                          value=value, expected=expected)


# ── Check registry ──────────────────────────────────────────────

CheckFn = Callable[[dict, dict], PreflightCheck]

_CHECKS: dict[str, CheckFn] = {}


def preflight_check(code: str):
    """Decorator to register a preflight check."""
    def decorator(fn: CheckFn) -> CheckFn:
        _CHECKS[code] = fn
        return fn
    return decorator


def get_checks() -> dict[str, CheckFn]:
    return dict(_CHECKS)


# ── Individual checks ──────────────────────────────────────────
#
# Every requirement is read from the profile, never defaulted here: the profile
# loader (profiles/) validates each profile against its schema and fills in the
# schema's defaults, so the schema and the YAML are the one source of a vendor
# fact. A hand-built profile that states no requirement gets a skip that says so.

def _requirement(profile: dict, *path: str):
    """profile[path...], or None when the profile does not state it."""
    node = profile
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _unstated(code: str, what: str) -> PreflightCheck:
    return _skip(code, f"Profile sets no {what}")


@preflight_check("trim-size")
def check_trim_size(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify PDF trim size matches vendor profile."""
    exp_w, exp_h = _requirement(profile, "trimSize", "width"), _requirement(profile, "trimSize", "height")
    if exp_w is None or exp_h is None:
        return _unstated("trim-size", "trim size")
    pdf_w = pdf_info.get("width_mm") or pdf_info.get("width", 0)
    pdf_h = pdf_info.get("height_mm") or pdf_info.get("height", 0)

    if not pdf_w or not pdf_h:
        return _fail("trim-size", "Could not determine PDF page dimensions",
                     suggestedFix="Ensure the PDF has a valid page box")

    if abs(pdf_w - exp_w) > 0.5 or abs(pdf_h - exp_h) > 0.5:
        return _fail(
            "trim-size",
            f"PDF dimensions {pdf_w:.1f}x{pdf_h:.1f} mm do not match profile {exp_w:.1f}x{exp_h:.1f} mm",
            suggestedFix="Set the PDF trim box to match the profile's trimSize",
            value={"width_mm": pdf_w, "height_mm": pdf_h},
            expected={"width_mm": exp_w, "height_mm": exp_h},
            policyUrl="https://kdp.amazon.com/en_US/help/topic/G201834340"
        )

    # Report what was measured, not what was required. Printing `exp_w`/`exp_h`
    # here made a pass line that read identically whether the probe had measured
    # the page or not -- the same shape of self-confirmation the constants in
    # `pdf_info` used to be.
    return _pass(
        "trim-size",
        f"Trim size {pdf_w:.1f}x{pdf_h:.1f} mm OK "
        f"(profile {exp_w:.1f}x{exp_h:.1f} mm, tolerance 0.5 mm)",
    )


@preflight_check("bleed")
def check_bleed(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify PDF has adequate bleed for the profile."""
    expected_bleed = _requirement(profile, "bleed", "all")
    if expected_bleed is None:
        return _unstated("bleed", "bleed")
    pdf_bleed = pdf_info.get("bleed_mm")

    if pdf_bleed is None:
        return _warn(
            "bleed",
            "Could not determine bleed: the PDF declares no TrimBox/MediaBox pair",
            suggestedFix="Emit a TrimBox so bleed can be measured against the profile",
            expected={"bleed_mm": expected_bleed},
        )

    if pdf_bleed < expected_bleed - 0.1:
        return _fail(
            "bleed",
            f"Bleed {pdf_bleed:.2f} mm is less than required {expected_bleed:.2f} mm",
            suggestedFix=f"Extend page content by {expected_bleed - pdf_bleed:.1f} mm on all sides",
            value={"bleed_mm": pdf_bleed}, expected={"bleed_mm": expected_bleed},
        )

    return _pass("bleed", f"Bleed {pdf_bleed:.2f} mm OK (requires {expected_bleed:.2f} mm)")


@preflight_check("min-pages")
def check_min_pages(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify page count meets vendor minimum."""
    page_count = pdf_info.get("page_count")
    min_pages = _requirement(profile, "minPages")
    if min_pages is None:
        return _unstated("min-pages", "minimum page count")

    if page_count is None:
        return _warn(
            "min-pages",
            "Page count could not be read from the PDF",
            suggestedFix="Check the file with a PDF-aware probe before printing",
            value=None, expected=min_pages,
        )

    if page_count < min_pages:
        return _fail(
            "min-pages",
            f"{page_count} pages is below the minimum of {min_pages}",
            suggestedFix="Add content to meet the minimum page requirement",
            value=page_count, expected=min_pages,
        )

    return _pass("min-pages", f"{page_count} pages >= {min_pages} minimum")


@preflight_check("max-pages")
def check_max_pages(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify page count does not exceed vendor maximum."""
    page_count = pdf_info.get("page_count")
    max_pages = _requirement(profile, "maxPages")
    if max_pages is None:
        return _unstated("max-pages", "maximum page count")

    if page_count is None:
        return _warn(
            "max-pages",
            "Page count could not be read from the PDF",
            suggestedFix="Check the file with a PDF-aware probe before printing",
            value=None, expected=max_pages,
        )

    if page_count > max_pages:
        return _fail(
            "max-pages",
            f"{page_count} pages exceeds the maximum of {max_pages}",
            suggestedFix="Split the book into multiple volumes or reduce content",
            value=page_count, expected=max_pages,
        )

    return _pass("max-pages", f"{page_count} pages <= {max_pages} maximum")


@preflight_check("page-multiple")
def check_page_multiple(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify page count is a valid multiple (e.g. 4 for KDP)."""
    page_count = pdf_info.get("page_count")
    multiple = _requirement(profile, "pageSizeMultiple")
    if multiple is None:
        return _unstated("page-multiple", "page multiple")

    if page_count is None:
        return _warn(
            "page-multiple",
            "Page count could not be read from the PDF",
            suggestedFix="Check the file with a PDF-aware probe before printing",
            value=None, expected=f"multiple of {multiple}",
        )

    if page_count % multiple != 0:
        # Parity pad handles this in the pipeline, but check anyway
        return _warn(
            "page-multiple",
            f"{page_count} pages is not a multiple of {multiple}",
            suggestedFix="The parity-pad stage should add blank pages",
            value=page_count, expected=f"multiple of {multiple}",
        )

    return _pass("page-multiple", f"{page_count} pages is a multiple of {multiple}")


@preflight_check("color-space")
def check_color_space(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify PDF uses the correct color space."""
    expected_cs = _requirement(profile, "pdfSpec", "colorSpace")
    if expected_cs is None:
        return _unstated("color-space", "colour space")
    pdf_cs = pdf_info.get("color_space", "unknown")

    if pdf_cs != expected_cs and expected_cs != "any":
        return _fail(
            "color-space",
            f"Color space '{pdf_cs}' does not match profile requirement '{expected_cs}'",
            suggestedFix=f"Convert the PDF to {expected_cs.upper()} using Ghostscript or ICC profile",
            value=pdf_cs, expected=expected_cs,
        )

    return _pass("color-space", f"Color space '{pdf_cs}' matches profile requirement")


@preflight_check("resolution")
def check_resolution(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Every raster image prints at the profile's minimum resolution or more.

    Effective resolution: the image's pixels over the size it is placed at on
    the page, so a 3000-pixel photo stretched across a spread can still fail.
    Below the minimum warns rather than fails -- vendors print a soft image,
    and whether that is acceptable is the publisher's call.
    """
    min_dpi = _requirement(profile, "pdfSpec", "minImageDpi")
    if min_dpi is None:
        return _unstated("resolution", "minimum image resolution")
    images = pdf_info.get("images") or {}
    if "placements" not in images:
        return _warn(
            "resolution",
            f"Image resolution not measured: {images.get('unmeasured', 'no image listing')}",
            suggestedFix="Run preflight where poppler-utils (pdfimages) is installed "
                         "(the worker image).",
            expected=min_dpi,
        )
    placements = images["placements"]
    if not placements:
        return _pass("resolution", "No raster images")
    lowest = min(ppi for _, ppi in placements)
    low = sorted({page for page, ppi in placements if ppi < min_dpi})
    if low:
        c = _warn(
            "resolution",
            f"Images print below {min_dpi} ppi on {_pages_phrase(low)} "
            f"(lowest {lowest:g} ppi)",
            suggestedFix="Replace them with higher-resolution originals, or place them smaller.",
            value=lowest, expected=min_dpi,
        )
        c.sourceRef = f"pdf#page={low[0]}"
        return c
    return _pass("resolution", f"{len(placements)} image placement(s), lowest {lowest:g} ppi")


@preflight_check("inside-margin")
def check_inside_margin(pdf_info: dict, profile: dict) -> PreflightCheck:
    """No page prints closer to the spine than the vendor's minimum for the
    book's page count. Measured from the press file's word boxes (margins.py),
    so it is what prints, whatever the design said."""
    bands = _requirement(profile, "bindingSpec", "minInsideMarginMm")
    if not bands:
        return _unstated("inside-margin", "minimum inside margin")
    page_count = pdf_info.get("page_count")
    if not page_count:
        return _warn("inside-margin", "Inside margin not checked: the page count is unknown")
    required = required_inside_margin(bands, page_count)
    measured = pdf_info.get("margins") or {}
    if "margins" not in measured:
        return _warn(
            "inside-margin",
            f"Inside margin not measured: {measured.get('unmeasured', 'no word boxes')}",
            suggestedFix="Run preflight where poppler-utils (pdftotext) is installed (the worker image).",
            expected=required,
        )
    margins = measured["margins"]
    if not margins:
        return _pass("inside-margin", "No page carries text")
    narrowest = min(margins.values())
    tight = sorted(page for page, mm in margins.items() if mm < required)
    if tight:
        c = _fail(
            "inside-margin",
            f"Text comes within {narrowest:g} mm of the spine on {_pages_phrase(tight)}; "
            f"a {page_count}-page book needs {required:g} mm",
            suggestedFix="Widen the design's inside margin (or its gutter) to the vendor minimum.",
            value=narrowest, expected=required,
        )
        c.sourceRef = f"pdf#page={tight[0]}"
        return c
    return _pass("inside-margin", f"Narrowest inside margin {narrowest:g} mm (needs {required:g} mm)")


@preflight_check("file-size")
def check_file_size(pdf_info: dict, profile: dict) -> PreflightCheck:
    """O4: Enforce FileSizeBudget — reject PDFs exceeding the vendor size limit."""
    size_budget = _requirement(profile, "proofSpec", "sizeBudgetBytes")
    if not size_budget:   # the schema's 0 means "no limit"
        return _unstated("file-size", "size budget")

    pdf_size = pdf_info.get("file_size_bytes", 0)

    if pdf_size > size_budget:
        return _fail(
            "file-size",
            f"PDF size {_fmt_bytes(pdf_size)} exceeds budget {_fmt_bytes(size_budget)}",
            suggestedFix="Reduce image resolution, use proof-quality rasterization, or split the book",
            value=pdf_size, expected=size_budget,
            policyUrl="#O4-two-artifact-policy",
        )

    return _pass("file-size", f"PDF size {_fmt_bytes(pdf_size)} within budget {_fmt_bytes(size_budget)}")


@preflight_check("embed-fonts")
def check_embed_fonts(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify all fonts are embedded in the PDF."""
    fonts = pdf_info.get("fonts", [])
    unembedded = [f for f in fonts if not f.get("embedded")]

    if unembedded:
        names = ", ".join(f.get("name", "?") for f in unembedded[:5])
        return _fail(
            "embed-fonts",
            f"{len(unembedded)} fonts not embedded: {names}",
            suggestedFix="Enable font embedding in the renderer or use outlines",
            value=len(unembedded), expected=0,
        )

    return _pass("embed-fonts", f"All {len(fonts)} fonts embedded")


@preflight_check("pdf-standard")
def check_pdf_standard(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify PDF standard compliance (PDF/X-1a, PDF/A, etc.)."""
    expected_standard = _requirement(profile, "pdfSpec", "standard")
    if expected_standard in (None, "none"):
        return _skip("pdf-standard", "No PDF standard required by profile")

    pdf_standard = pdf_info.get("pdf_standard", "none")
    if pdf_standard != expected_standard:
        return _warn(
            "pdf-standard",
            f"PDF standard '{pdf_standard}' does not match profile '{expected_standard}'",
            suggestedFix=f"Run Ghostscript with -dPDFX to produce {expected_standard}",
            value=pdf_standard, expected=expected_standard,
        )

    return _pass("pdf-standard", f"PDF standard '{pdf_standard}' matches profile")


@preflight_check("interactive-content")
def check_interactive_content(pdf_info: dict, profile: dict) -> PreflightCheck:
    """A print PDF carries no JavaScript, launch actions, form fields or annotations.

    PDF/X permits only PrinterMark and TrapNet annotations; printers reject
    anything else, and a form field or comment can print on the page.
    `finish-gs` drops link annotations (`-dPreserveAnnots=false`); this is the
    gate that proves it did, on the file actually delivered.
    """
    found = pdf_info.get("interactive")
    if found is None:
        return _warn(
            "interactive-content",
            "JavaScript, forms and annotations not measured: the PDF stores its "
            "objects in compressed object streams, which byte scanning cannot read",
            suggestedFix="Deliver the Ghostscript PDF/X output (PDF 1.3 has no object streams).",
        )
    if found:
        return _fail(
            "interactive-content",
            f"Print PDF contains {', '.join(found)}",
            suggestedFix="Remove them in the source, or re-run finish-gs, which drops annotations.",
            value=found, expected=[],
        )
    return _pass("interactive-content", "No JavaScript, forms or annotations")


# The standards that forbid transparency. PDF/X-4 and PDF/A-2 carry it live.
NO_TRANSPARENCY_STANDARDS = frozenset({"pdfx-1a", "pdfx-3"})


@preflight_check("transparency")
def check_transparency(pdf_info: dict, profile: dict) -> PreflightCheck:
    """No live transparency under a standard that forbids it (PDF/X-1a, X-3).

    A soft mask, constant alpha below 1, a blend mode other than Normal, or a
    transparency group has no defined result in a PDF/X-1a RIP: vendors reject
    the file, or it prints as something else. Ghostscript flattens transparency
    at PDF 1.3, so on a `finish-gs` press file this proves it did.
    """
    standard = _requirement(profile, "pdfSpec", "standard")
    if standard is None:
        return _unstated("transparency", "PDF standard")
    if standard not in NO_TRANSPARENCY_STANDARDS:
        return _skip("transparency", f"{standard} permits live transparency")
    found = pdf_info.get("transparency")
    if found is None:
        return _warn(
            "transparency",
            "Transparency not measured: the PDF stores its objects in compressed "
            "object streams, which byte scanning cannot read",
            suggestedFix="Deliver the Ghostscript PDF/X output (PDF 1.3 has no object streams).",
        )
    if found:
        return _fail(
            "transparency",
            f"{standard} forbids transparency; the PDF uses {', '.join(found)}",
            suggestedFix="Flatten transparency (finish-gs does at PDF 1.3), or give "
                         "images an opaque background instead of an alpha channel.",
            value=found, expected=[],
        )
    return _pass("transparency", "No live transparency")


# Rich black text: black (K at least RICH_BLACK_MIN_K) that also carries C+M+Y of
# RICH_BLACK_MIN_CMY or more. Four plates must then register on every stroke of
# small type. RGB black converts to C72 M67 Y67 K88; a designer's dark colour
# rarely goes past K50, so it is not caught.
RICH_BLACK_MIN_K = 70
RICH_BLACK_MIN_CMY = 30


def is_rich_black(colour: tuple[int, int, int, int]) -> bool:
    c, m, y, k = colour
    return k >= RICH_BLACK_MIN_K and c + m + y >= RICH_BLACK_MIN_CMY


def parse_pdfimages_list(listing: str) -> list[tuple[int, float]]:
    """(page, effective ppi) for every placed raster image in `pdfimages -list`.

    Only `image` rows count: `smask` and `mask` rows are an image's alpha or
    stencil, not a picture of their own, and `stencil` rows are one-bit masks
    whose resolution requirement is line art's, not a photo's. The lower of the
    two axes is the image's resolution -- the direction it is stretched in.
    """
    placements = []
    for line in listing.splitlines()[2:]:          # header and rule
        fields = line.split()
        if len(fields) < 14 or fields[2] != "image":
            continue
        try:
            placements.append((int(fields[0]), min(float(fields[12]), float(fields[13]))))
        except ValueError:                           # an unplaced image lists "-"
            continue
    return placements


def measure_image_ppi(pdf_path: Path) -> dict:
    """The effective resolution of every image placement, via poppler's
    `pdfimages -list`: it walks each page's content stream and reports pixels
    over placed size, which a byte scan cannot. When the tool is missing or
    fails, the reason is returned instead, and the check says "not measured"."""
    binary = shutil.which("pdfimages")
    if binary is None:
        return {"unmeasured": "pdfimages (poppler-utils) is not installed"}
    try:
        listing = subprocess.run([binary, "-list", str(pdf_path)], capture_output=True,
                                 text=True, timeout=PDFIMAGES_TIMEOUT_S, check=True).stdout
    except (subprocess.SubprocessError, OSError) as error:
        return {"unmeasured": f"pdfimages failed: {str(error)[:300]}"}
    return {"placements": parse_pdfimages_list(listing)}


def measure_ink(pdf_path: Path) -> dict:
    """Per page: the highest total ink, and whether any text is rich black.

    Both come from Ghostscript renders (`ghostscript.page_colours`); a byte scan
    cannot see what colour a page prints. When Ghostscript is missing or fails,
    the reason is returned instead, and both checks say "not measured".
    """
    if find_binary() is None:
        return {"unmeasured": "Ghostscript is not installed"}
    try:
        tac = [max(map(sum, colours), default=0) for colours in page_colours(pdf_path)]
        rich = [number for number, colours in
                enumerate(page_colours(pdf_path, text_only=True), start=1)
                if any(map(is_rich_black, colours))]
    except GhostscriptError as error:
        return {"unmeasured": str(error)[:300]}
    return {"tac": tac, "rich_black_text_pages": rich}


def _ink_unmeasured(code: str, ink: dict) -> PreflightCheck:
    return _warn(code, f"Not measured: {ink.get('unmeasured', 'no render of the pages')}",
                 suggestedFix="Run preflight where Ghostscript is installed (the worker image).")


@preflight_check("ink-coverage")
def check_ink_coverage(pdf_info: dict, profile: dict) -> PreflightCheck:
    """No point on any page carries more ink than the press allows."""
    ink = pdf_info.get("ink") or {}
    if "tac" not in ink:
        return _ink_unmeasured("ink-coverage", ink)
    limit = _requirement(profile, "pdfSpec", "maxInkCoverage")
    if limit is None:
        return _unstated("ink-coverage", "ink limit")
    over = [number for number, tac in enumerate(ink["tac"], start=1) if tac > limit]
    highest = max(ink["tac"], default=0)
    if over:
        c = _fail("ink-coverage",
                  f"Total ink reaches {highest}%, over the {limit}% limit, on {_pages_phrase(over)}",
                  suggestedFix="Convert the images with the vendor's CMYK profile, and set "
                               "black as K only rather than four-colour black.",
                  value=highest, expected=limit)
        c.sourceRef = f"pdf#page={over[0]}"
        return c
    return _pass("ink-coverage", f"Total ink at most {highest}% (limit {limit}%)")


@preflight_check("rich-black-text")
def check_rich_black_text(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Black text is set in K alone, not in four-colour black."""
    ink = pdf_info.get("ink") or {}
    if "rich_black_text_pages" not in ink:
        return _ink_unmeasured("rich-black-text", ink)
    pages = ink["rich_black_text_pages"]
    if pages:
        c = _fail("rich-black-text",
                  f"Black text is printed in four inks (rich black) on {_pages_phrase(pages)}; "
                  f"any misregistration blurs it",
                  suggestedFix="Set black text as K only (CMYK 0 0 0 100).",
                  value=len(pages), expected=0)
        c.sourceRef = f"pdf#page={pages[0]}"
        return c
    return _pass("rich-black-text", "Black text is K only")


def _skip(code: str, humanMessage: str) -> PreflightCheck:
    return PreflightCheck(code=code, status="skip", severity="info",
                          humanMessage=humanMessage)


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    else:
        return f"{n / 1024 / 1024:.1f} MB"


# ── Composition (typographic) defects ──────────────────────────

# Severities mirror platform/pagescan/src/lib.rs's own policy rather than
# inventing a second one: an orphan is an error there (weight 4.0), a widow
# (3.0) and a runt (2.0) are warnings. That crate is not reachable from this
# runtime -- it has no PyO3 module and the pipeline is pure Python -- so the
# decision is reused, not the code.
_COMPOSITION_FLAGS = {
    "hasOrphans": "orphans",
    "hasWidows": "widows",
    "hasRunts": "runts",
}


def scan_composition(pagemap: dict | None) -> dict:
    """Count composition defects over a `pagemap/1`'s MEASURED pages.

    A page counts as measured only if it actually carries the flags. Absent
    means "not measured" and `false` means "measured, none found" -- the
    property `stages.paginate_stage._measure_pages` exists to preserve, and the
    reason an unmeasured book must not report as clean here. Pages the renderer
    could not measure (the Typst path lays out without a box tree to walk) are
    counted in `pages` and excluded from `measured`.
    """
    pages = (pagemap or {}).get("pages") or []
    found: dict = {"pages": len(pages), "measured": 0,
                   "orphans": [], "widows": [], "runts": []}
    for page in pages:
        if not isinstance(page, dict) or not any(f in page for f in _COMPOSITION_FLAGS):
            continue
        found["measured"] += 1
        number = page.get("pageNumber")
        for flag, bucket in _COMPOSITION_FLAGS.items():
            if page.get(flag) is True:
                found[bucket].append(number)
    return found


def _pages_phrase(numbers: list) -> str:
    """"page 4" / "pages 4, 9" / "pages 4, 9 and 3 more" -- bounded (D6)."""
    shown = ", ".join(str(n) for n in numbers[:6])
    more = len(numbers) - 6
    return (f"page{'s' if len(numbers) != 1 else ''} {shown}"
            + (f" and {more} more" if more > 0 else ""))


@preflight_check("composition")
def check_composition(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Typographic composition gate, read off the render path's measured pagemap.

    Three outcomes, and the middle one is the point:
      - no page measured -> WARN. Not a pass: nothing looked at the composition,
        which is exactly what the pagescan path used to report as clean.
      - orphans over the profile's allowance -> FAIL, blocking delivery.
      - widows or runts -> WARN. Real defects, but ones a typesetter routinely
        accepts; failing on them would block every book ever set.
    """
    found = scan_composition(pdf_info.get("pagemap"))

    if found["measured"] == 0:
        c = _warn(
            "composition",
            f"Composition not measured on any of {found['pages']} page(s) — "
            f"widows, orphans and runts are UNKNOWN, not absent",
            suggestedFix="Render via the CSS path (PUBLISHER_RENDER_ENGINE=css), "
                         "which walks the renderer's box tree; the Typst path "
                         "composes without one.",
            value={"pages": found["pages"], "measured": 0},
        )
        c.sourceRef = "pagemap/1"
        return c

    # Absent allows none: an orphan is a defect unless the profile says otherwise.
    allowed = int(_requirement(profile, "composition", "maxOrphanPages") or 0)
    orphans, widows, runts = found["orphans"], found["widows"], found["runts"]

    if len(orphans) > allowed:
        c = _fail(
            "composition",
            f"{len(orphans)} page(s) begin a paragraph with a single line "
            f"stranded at the foot: {_pages_phrase(orphans)}",
            suggestedFix="Raise `orphans` in the DesignSpec's paragraph style, or "
                         "set composition.maxOrphanPages in the vendor profile if "
                         "this house style tolerates them.",
            value=len(orphans),
            expected=allowed,
        )
        c.sourceRef = f"pagemap/1#page={orphans[0]}"
        return c

    if widows or runts:
        parts = []
        if widows:
            parts.append(f"{len(widows)} widow page(s) ({_pages_phrase(widows)})")
        if runts:
            parts.append(f"{len(runts)} runt page(s) ({_pages_phrase(runts)})")
        c = _warn(
            "composition",
            "Composition defects found: " + "; ".join(parts),
            suggestedFix="Adjust tracking or the hyphenation zone on the offending "
                         "paragraphs; neither defect blocks delivery.",
            value={"widows": len(widows), "runts": len(runts)},
        )
        c.sourceRef = f"pagemap/1#page={(widows or runts)[0]}"
        return c

    return _pass(
        "composition",
        f"No widows, orphans or runts across {found['measured']} measured page(s)",
    )


# ── Runner ─────────────────────────────────────────────────────

def run_preflight(pdf_path: str | Path, profile: dict,
                  created_at: str | None = None,
                  pagemap: dict | None = None) -> PreflightReport:
    """
    Run all preflight checks against a PDF and vendor profile.
    
    This is the hard gate — an error-level check failure blocks delivery.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return PreflightReport(
            status="fail",
            profileId=profile.get("name"),
            checks=[_fail("file-not-found", f"PDF not found: {pdf_path}")],
        )

    pdf_info = probe_pdf(pdf_path)
    # Composition is measured by the render path, not readable from the PDF
    # bytes, so it arrives alongside rather than out of `probe_pdf`. Passed
    # through the same dict so the CheckFn signature stays (pdf_info, profile).
    pdf_info["pagemap"] = pagemap

    # A file that is not a PDF cannot be preflighted, and must never collect a
    # row of passes because each individual check found its field absent.
    if not pdf_info["is_pdf"]:
        return PreflightReport(
            status="fail",
            profileId=profile.get("name"),
            checks=[_fail(
                "file-format",
                f"{pdf_path.name} is not a PDF (no %PDF- header)",
                suggestedFix="Check that the finish stage produced a real PDF.",
            )],
            summary={"passed": 0, "failed": 1, "warnings": 0, "policyViolations": 1},
            profileVersion=profile.get("vendorProfileVersion"),
            createdAt=created_at,
        )

    pdf_info["ink"] = measure_ink(pdf_path)
    pdf_info["images"] = measure_image_ppi(pdf_path)
    pdf_info["margins"] = measure_inside_margins(pdf_path, pdf_info["media_box"], pdf_info["trim_box"])

    checks = []
    for code, check_fn in sorted(_CHECKS.items()):
        try:
            result = check_fn(pdf_info, profile)
            checks.append(result)
        except Exception as e:
            checks.append(_fail(code, f"Check crashed: {e}"))

    # Compute summary
    summary = {"passed": 0, "failed": 0, "warnings": 0, "policyViolations": 0}
    for c in checks:
        if c.status == "pass":
            summary["passed"] += 1
        elif c.status == "fail":
            summary["failed"] += 1
            if c.code == "file-size":
                summary["policyViolations"] += 1
        elif c.status == "warn":
            summary["warnings"] += 1

    overall = "fail" if summary["failed"] > 0 else "warn" if summary["warnings"] > 0 else "pass"

    return PreflightReport(
        status=overall,
        profileId=profile.get("name"),
        pageCount=pdf_info.get("page_count"),
        checks=checks,
        summary=summary,
        profileVersion=profile.get("vendorProfileVersion"),
        # Caller-supplied and cache-key-derived, never wall-clock: this report is
        # written to the CAS, so a `datetime.now()` here would change the artifact
        # hash on every run over identical input (ARCHITECTURE.md §2.5 / D5).
        createdAt=created_at,
    )



_BOX_RE = {
    "media": re.compile(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"),
    "trim": re.compile(rb"/TrimBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"),
}
# `(?![s\w])` excludes `/Pages` (the page-tree node) while still matching the
# `/Type/Page/MediaBox` that Ghostscript emits. An earlier version also excluded
# a following `/`, which meant it matched weasyprint's spaced-out `/Type /Page`
# but not a single page of any Ghostscript output: the 168-page press file
# probed as 0 pages, and `max(..., 1)` quietly rounded that up to 1, so
# min-pages passed on a book the probe could not see.
_PAGE_RE = re.compile(rb"/Type\s*/Page(?![s\w])")
# The page tree's own tally. Preferred over counting page objects because it is
# one number written by the producer rather than a sum over a byte scan.
_PAGE_COUNT_RE = re.compile(rb"/Type\s*/Pages\b[^>]{0,400}?/Count\s+(\d+)", re.DOTALL)
_COUNT_RE = re.compile(rb"/Count\s+(\d+)")
_BASEFONT_RE = re.compile(rb"/BaseFont\s*/([#\w+-]+)")
_FONTFILE_RE = re.compile(rb"/FontFile[23]?\b")
# A composite (Type0) font writes /BaseFont TWICE for ONE embedded program:
# once on the Type0 object, carrying its CMap suffix (`Foo-Regular-Identity-H`),
# and once on the descendant CIDFont without it (`Foo-Regular`). Counting those
# as two fonts against one /FontFile3 reports the book's only face as "not
# embedded" and blocks delivery on a font that IS embedded. Every real Typst or
# weasyprint PDF of a book is composite, so this is the common case.
_CMAP_SUFFIX_RE = re.compile(r"-(?:Identity|Uni(?:JIS|GB|CNS|KS)[\w-]*?)-[HV]$")


# Stream bodies are compressed binary: 7 MB of it matched `/JS` once by chance in
# a real book. Dictionaries sit outside them, so they are cut before scanning.
_STREAM_RE = re.compile(rb"(?<![\w/])stream\r?\n.*?endstream", re.DOTALL)
_OBJSTM_RE = re.compile(rb"/Type\s*/ObjStm\b")
# Every annotation subtype but PrinterMark and TrapNet, the two PDF/X permits.
_ANNOT_RE = re.compile(
    rb"/Subtype\s*/(Text|Link|FreeText|Line|Square|Circle|Polygon|PolyLine|Highlight"
    rb"|Underline|Squiggly|StrikeOut|Stamp|Caret|Ink|Popup|FileAttachment|Sound|Movie"
    rb"|Widget|Screen|Watermark|3D|Redact|RichMedia|Projection)\b")
_INTERACTIVE_RE = {
    "JavaScript": re.compile(rb"/(?:JavaScript|JS)\b"),
    "launch action": re.compile(rb"/S\s*/Launch\b"),
    "trigger actions (/AA)": re.compile(rb"/AA\b"),
    "form fields": re.compile(rb"/AcroForm\b"),
}


# Live transparency, as PDF/X-1a's RIP would meet it.
_SMASK_RE = re.compile(rb"/SMask(?!\w)\s*(/?\w+)")              # /SMask /None is opaque
_ALPHA_RE = re.compile(rb"/(?:CA|ca)\s+([0-9.]+)")
_BLEND_RE = re.compile(rb"/BM\s*(\[[^\]]*\]|/\w+)")
_OPAQUE_BLENDS = {b"Normal", b"Compatible"}
_GROUP_RE = re.compile(rb"/S\s*/Transparency(?!\w)")


def _transparency(raw: bytes) -> list[str] | None:
    """The kinds of live transparency a PDF uses, sorted; None when it cannot be
    seen (dictionaries hidden in an /ObjStm, as for `_interactive_content`)."""
    dicts = _STREAM_RE.sub(b"", raw)
    if _OBJSTM_RE.search(dicts):
        return None
    found = set()
    if any(m.group(1) != b"/None" for m in _SMASK_RE.finditer(dicts)):
        found.add("soft masks")
    if any(float(m.group(1)) < 1 for m in _ALPHA_RE.finditer(dicts)
           if m.group(1).strip(b".")):
        found.add("constant alpha below 1")
    blends = {name for m in _BLEND_RE.finditer(dicts)
              for name in re.findall(rb"/(\w+)", m.group(1))}
    found |= {f"{name.decode()} blend mode" for name in blends - _OPAQUE_BLENDS}
    if _GROUP_RE.search(dicts):
        found.add("transparency groups")
    return sorted(found)


def _interactive_content(raw: bytes) -> list[str] | None:
    """What a print PDF must not carry, sorted; None when it cannot be seen.

    Objects inside a compressed object stream (/ObjStm, PDF 1.5+) are invisible
    to a byte scan, so a file that has one is unmeasured, not clean.
    """
    dicts = _STREAM_RE.sub(b"", raw)
    if _OBJSTM_RE.search(dicts):
        return None
    found = [name for name, pattern in _INTERACTIVE_RE.items() if pattern.search(dicts)]
    found += [f"{kind.decode()} annotation" for kind in
              sorted({m.group(1) for m in _ANNOT_RE.finditer(dicts)})]
    return sorted(found)


def _count_pages(raw: bytes) -> int | None:
    """Number of pages, or None when the byte stream does not reveal it.

    None rather than a fallback of 1: a page count is the input to the
    min/max/multiple checks and to spine width downstream, so guessing here
    means those checks pass on a number nobody measured.
    """
    objects = len(_PAGE_RE.findall(raw))
    if objects:
        return objects

    # No page objects in plaintext -- the file may store them in compressed
    # object streams. The page tree's /Count usually survives; take the largest
    # (nested /Pages nodes each carry their own subtree tally).
    counts = [int(m) for m in _COUNT_RE.findall(raw)] if _PAGE_COUNT_RE.search(raw) else []
    if counts:
        return max(counts)

    return None


def _box(raw: bytes, which: str) -> tuple[float, float, float, float] | None:
    m = _BOX_RE[which].search(raw)
    if not m:
        return None
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return x0, y0, x1, y1


def probe_pdf(pdf_path: Path) -> dict:
    """Measure the facts preflight checks against, from the PDF itself.

    This function exists because `pdf_info` used to be a dict of constants:
    `pdf_standard` was hard-coded "none", `color_space` "cmyk", `effective_dpi`
    300, `fonts` empty, and -- worst -- `width_mm`/`height_mm`/`bleed_mm` were
    copied straight out of the vendor profile the checks then compared them to.
    Trim-size and bleed therefore compared the profile with itself and could not
    fail; embed-fonts passed vacuously over an empty list. The gate reported
    nine passes on any input whatsoever, including a blank page.

    Values that genuinely cannot be read from the byte stream are reported as
    `None` so their checks can say "unknown" rather than assert a flattering
    default.
    """
    raw = pdf_path.read_bytes()

    is_pdf = raw[:5] == b"%PDF-"
    has_pdfx = b"/GTS_PDFX" in raw and b"/OutputIntent" in raw

    media = _box(raw, "media")
    trim = _box(raw, "trim") or media

    width_mm = height_mm = None
    if trim:
        width_mm = round(abs(trim[2] - trim[0]) / PT_PER_MM, 2)
        height_mm = round(abs(trim[3] - trim[1]) / PT_PER_MM, 2)

    # Bleed is the margin the media box extends beyond the trim box, per side.
    bleed_mm = None
    if media and trim:
        bleed_mm = round(
            min(
                abs(trim[0] - media[0]),
                abs(trim[1] - media[1]),
                abs(media[2] - trim[2]),
                abs(media[3] - trim[3]),
            )
            / PT_PER_MM,
            2,
        )

    has_cmyk = b"/DeviceCMYK" in raw
    has_rgb = b"/DeviceRGB" in raw
    if has_cmyk and has_rgb:
        color_space = "mixed"
    elif has_cmyk:
        color_space = "cmyk"
    elif has_rgb:
        color_space = "rgb"
    else:
        color_space = "unknown"

    # Font objects and embedded font programs are counted, not paired: matching
    # each /BaseFont to its own /FontFile needs real object-graph parsing. If
    # every font object has an accompanying font program the set is embedded;
    # otherwise report the shortfall rather than guessing which one is missing.
    font_names = sorted({
        _CMAP_SUFFIX_RE.sub("", m.group(1).decode("latin-1"))
        for m in _BASEFONT_RE.finditer(raw)
    })
    embedded_count = len(_FONTFILE_RE.findall(raw))
    fonts = [
        {"name": name, "embedded": i < embedded_count}
        for i, name in enumerate(font_names)
    ]

    return {
        "is_pdf": is_pdf,
        "file_size_bytes": pdf_path.stat().st_size,
        "page_count": _count_pages(raw),
        "color_space": color_space,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "bleed_mm": bleed_mm,
        "media_box": media,
        "trim_box": trim,
        "fonts": fonts,
        "pdf_standard": "pdfx-1a" if has_pdfx else "none",
        "interactive": _interactive_content(raw),
        "transparency": _transparency(raw),
    }
