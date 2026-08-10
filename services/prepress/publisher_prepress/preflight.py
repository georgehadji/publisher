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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


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

@preflight_check("trim-size")
def check_trim_size(pdf_info: dict, profile: dict) -> PreflightCheck:
    """Verify PDF trim size matches vendor profile."""
    expected = profile.get("trimSize", {})
    pdf_w = pdf_info.get("width_mm") or pdf_info.get("width", 0)
    pdf_h = pdf_info.get("height_mm") or pdf_info.get("height", 0)

    if not pdf_w or not pdf_h:
        return _fail("trim-size", "Could not determine PDF page dimensions",
                     suggestedFix="Ensure the PDF has a valid page box")

    exp_w = expected.get("width", 152.4)
    exp_h = expected.get("height", 228.6)

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
    expected_bleed = profile.get("bleed", {}).get("all", 3.0)
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
    min_pages = profile.get("minPages", 1)

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
    max_pages = profile.get("maxPages", 2000)

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
    multiple = profile.get("pageSizeMultiple", 4)

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
    expected_cs = profile.get("pdfSpec", {}).get("colorSpace", "cmyk")
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
    """Verify image resolution meets minimum DPI."""
    min_dpi = profile.get("proofSpec", {}).get("dpi", 300)
    pdf_dpi = pdf_info.get("effective_dpi")

    if pdf_dpi is None:
        # Byte scanning cannot decode image streams. Saying so is the honest
        # result; the previous hard-coded 300 silently passed this check for
        # every book, including one full of 72-DPI screenshots.
        return _warn(
            "resolution",
            "Effective image resolution not measured (no PDF image decoder available)",
            suggestedFix="Install PyMuPDF in the worker image to enable DPI checking",
            expected=min_dpi,
        )

    if pdf_dpi < min_dpi:
        return _warn(
            "resolution",
            f"Effective resolution {pdf_dpi} DPI is below {min_dpi} DPI",
            suggestedFix="Use higher-resolution source images",
            value=pdf_dpi, expected=min_dpi,
        )

    return _pass("resolution", f"Resolution {pdf_dpi} DPI OK")


@preflight_check("file-size")
def check_file_size(pdf_info: dict, profile: dict) -> PreflightCheck:
    """O4: Enforce FileSizeBudget — reject PDFs exceeding the vendor size limit."""
    size_budget = profile.get("proofSpec", {}).get("sizeBudgetBytes")
    if size_budget is None:
        return _skip("file-size", "No size budget configured in profile")

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
    expected_standard = profile.get("pdfSpec", {}).get("standard", "none")
    if expected_standard == "none":
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


# ── Runner ─────────────────────────────────────────────────────

def run_preflight(pdf_path: str | Path, profile: dict,
                  created_at: str | None = None) -> PreflightReport:
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


_PT_PER_MM = 72.0 / 25.4

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
        width_mm = round(abs(trim[2] - trim[0]) / _PT_PER_MM, 2)
        height_mm = round(abs(trim[3] - trim[1]) / _PT_PER_MM, 2)

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
            / _PT_PER_MM,
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
    font_names = sorted({m.group(1).decode("latin-1") for m in _BASEFONT_RE.finditer(raw)})
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
        # Raster resolution needs image-stream decoding, which byte scanning
        # cannot do. Unknown, and reported as such.
        "effective_dpi": None,
        "fonts": fonts,
        "pdf_standard": "pdfx-1a" if has_pdfx else "none",
    }
