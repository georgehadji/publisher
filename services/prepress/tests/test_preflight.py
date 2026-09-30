"""Tests for preflight rule engine."""

import json
import tempfile
from pathlib import Path

from publisher_prepress.preflight import (
    PreflightCheck, PreflightReport, run_preflight,
    check_trim_size, check_bleed, check_min_pages, check_max_pages,
    check_file_size, check_color_space, check_page_multiple,
    _count_pages,
    check_embed_fonts, check_pdf_standard, check_resolution,
    _CHECKS,
)


def test_preflight_trim_size_pass():
    pdf_info = {"width_mm": 152.4, "height_mm": 228.6}
    profile = {"trimSize": {"width": 152.4, "height": 228.6}}
    result = check_trim_size(pdf_info, profile)
    assert result.status == "pass"
    assert "trim-size" in result.code


def test_preflight_trim_size_fail():
    pdf_info = {"width_mm": 200, "height_mm": 300}
    profile = {"trimSize": {"width": 152.4, "height": 228.6}}
    result = check_trim_size(pdf_info, profile)
    assert result.status == "fail"


def test_preflight_bleed_pass():
    pdf_info = {"bleed_mm": 3.175}
    profile = {"bleed": {"all": 3.175}}
    result = check_bleed(pdf_info, profile)
    assert result.status == "pass"


def test_preflight_bleed_fail():
    pdf_info = {"bleed_mm": 1.0}
    profile = {"bleed": {"all": 3.0}}
    result = check_bleed(pdf_info, profile)
    assert result.status == "fail"


def test_preflight_min_pages_pass():
    pdf_info = {"page_count": 50}
    profile = {"minPages": 24}
    result = check_min_pages(pdf_info, profile)
    assert result.status == "pass"


def test_preflight_min_pages_fail():
    pdf_info = {"page_count": 10}
    profile = {"minPages": 24}
    result = check_min_pages(pdf_info, profile)
    assert result.status == "fail"


def test_preflight_max_pages_pass():
    pdf_info = {"page_count": 200}
    profile = {"maxPages": 828}
    result = check_max_pages(pdf_info, profile)
    assert result.status == "pass"


def test_preflight_page_multiple_pass():
    pdf_info = {"page_count": 200}
    profile = {"pageSizeMultiple": 4}
    result = check_page_multiple(pdf_info, profile)
    assert result.status == "pass"


def test_preflight_page_multiple_warn():
    pdf_info = {"page_count": 201}
    profile = {"pageSizeMultiple": 4}
    result = check_page_multiple(pdf_info, profile)
    assert result.status == "warn"


def test_preflight_color_space_pass():
    pdf_info = {"color_space": "cmyk"}
    profile = {"pdfSpec": {"colorSpace": "cmyk"}}
    result = check_color_space(pdf_info, profile)
    assert result.status == "pass"


def test_preflight_file_size_pass():
    pdf_info = {"file_size_bytes": 1000000}
    profile = {"proofSpec": {"sizeBudgetBytes": 50000000}}
    result = check_file_size(pdf_info, profile)
    assert result.status == "pass"


def test_preflight_file_size_fail():
    pdf_info = {"file_size_bytes": 100000000}
    profile = {"proofSpec": {"sizeBudgetBytes": 50000000}}
    result = check_file_size(pdf_info, profile)
    assert result.status == "fail"
    assert "budget" in result.humanMessage.lower()


def test_preflight_resolution_pass():
    pdf_info = {"effective_dpi": 300}
    profile = {"pdfSpec": {"minImageDpi": 300}}
    result = check_resolution(pdf_info, profile)
    assert result.status == "pass"
    # A profile that states no minimum is not a pass by assumption.
    assert check_resolution(pdf_info, {}).status == "skip"


def test_preflight_embed_fonts_pass():
    pdf_info = {"fonts": [{"name": "Garamond", "embedded": True}]}
    profile = {}
    result = check_embed_fonts(pdf_info, profile)
    assert result.status == "pass"


def test_preflight_embed_fonts_fail():
    pdf_info = {"fonts": [{"name": "BadFont", "embedded": False}]}
    profile = {}
    result = check_embed_fonts(pdf_info, profile)
    assert result.status == "fail"


def test_preflight_pdf_standard_skip():
    pdf_info = {}
    profile = {"pdfSpec": {"standard": "none"}}
    result = check_pdf_standard(pdf_info, profile)
    assert result.status == "skip"


def test_all_checks_registered():
    """Every check function should be in the registry."""
    assert "trim-size" in _CHECKS
    assert "bleed" in _CHECKS
    assert "min-pages" in _CHECKS
    assert "max-pages" in _CHECKS
    assert "file-size" in _CHECKS
    assert "color-space" in _CHECKS
    assert "resolution" in _CHECKS
    assert "embed-fonts" in _CHECKS
    assert "pdf-standard" in _CHECKS
    assert "page-multiple" in _CHECKS
    assert len(_CHECKS) >= 10


def test_run_preflight_on_real_pdf():
    """Run preflight against a synthetic PDF."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a minimal valid PDF
        pdf_path = Path(tmp) / "test.pdf"
        pdf_path.write_text("%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000060 00000 n \n0000000124 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n220\n%%EOF")
        
        profile = {
            "name": "Generic 6x9",
            "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
            "bleed": {"all": 3.0},
            "pdfSpec": {"version": "1.7", "standard": "pdfx-1a", "colorSpace": "cmyk"},
            "proofSpec": {"dpi": 300, "sizeBudgetBytes": 50000000},
            "minPages": 1,
            "maxPages": 2000,
            "pageSizeMultiple": 1,
        }
        
        report = run_preflight(pdf_path, profile)
        assert report.profileId == "Generic 6x9"
        # `status in (pass, warn, fail)` is the field's whole domain and
        # `summary["passed"] >= 0` is a non-negative counter — neither can fail.
        # run_preflight also converts a crashed check into a fail entry, so a run where
        # every check threw satisfied both. Assert real outcomes instead.
        assert report.checks, "no checks ran at all"
        assert report.summary["passed"] + report.summary["failed"] +                report.summary["warnings"] <= len(report.checks)
        assert report.status == ("fail" if report.summary["failed"] else
                                 "warn" if report.summary["warnings"] else "pass")


def test_preflight_report_serialization():
    """PreflightReport should serialize cleanly to dict."""
    report = PreflightReport(
        status="pass",
        profileId="Test",
        pageCount=100,
        checks=[
            PreflightCheck(code="test", status="pass", severity="info",
                          humanMessage="OK")
        ],
    )
    d = report.to_dict()
    assert d["status"] == "pass"
    assert len(d["checks"]) == 1
    assert d["checks"][0]["code"] == "test"


# --- page counting ------------------------------------------------------
#
# The regression these cover: `_PAGE_RE` excluded a `/` after "Page", which
# matches weasyprint's `/Type /Page /Parent` but never Ghostscript's
# `/Type/Page/MediaBox`. Every press file probed as 0 pages, and a
# `max(count, 1)` turned that into a confident "1 page" that min-pages then
# passed. Both producers' spellings are asserted here so a future tightening
# of the pattern cannot silently drop one again.

def test_count_pages_reads_ghostscript_spelling():
    raw = b"<</Type/Page/MediaBox [0 0 430 649]>>" * 168
    assert _count_pages(raw) == 168


def test_count_pages_reads_spaced_spelling():
    raw = b"<< /Type /Page /Parent 3 0 R >>" * 7
    assert _count_pages(raw) == 7


def test_count_pages_does_not_count_the_page_tree_node():
    raw = b"<</Type /Pages /Count 2>>" + b"<</Type/Page/MediaBox[0 0 1 1]>>" * 2
    assert _count_pages(raw) == 2


def test_count_pages_falls_back_to_the_page_tree_count():
    """Page objects can live in compressed object streams; /Count survives."""
    raw = b"<</Type /Pages /Kids[5 0 R]/Count 168>>"
    assert _count_pages(raw) == 168


def test_count_pages_is_none_when_unreadable():
    """Not 1. A guessed page count is one the min/max checks pass on blindly."""
    assert _count_pages(b"%PDF-1.7\nnothing useful here\n%%EOF") is None


def test_page_checks_warn_rather_than_pass_on_an_unknown_count():
    info = {"page_count": None}
    profile = {"minPages": 24, "maxPages": 800, "pageSizeMultiple": 4}
    assert check_min_pages(info, profile).status == "warn"
    assert check_max_pages(info, profile).status == "warn"
    assert check_page_multiple(info, profile).status == "warn"


# --- interactive content --------------------------------------------------

from publisher_prepress.preflight import _interactive_content, check_interactive_content

def _page(extra: bytes = b"") -> bytes:
    return b"%PDF-1.3\n1 0 obj<</Type/Page/MediaBox[0 0 1 1]" + extra + b">>endobj\n"


def test_interactive_content_clean_press_file():
    assert _interactive_content(_page()) == []
    assert check_interactive_content({"interactive": []}, {}).status == "pass"


def test_interactive_content_finds_what_a_printer_rejects():
    raw = (_page(b"/Annots[2 0 R]")
           + b"2 0 obj<</Type/Annot/Subtype/Link/A<</S/URI/URI(http://x)>>>>endobj\n"
           + b"3 0 obj<</Type/Annot/Subtype/Widget>>endobj\n"
           + b"4 0 obj<</Type/Catalog/AcroForm 5 0 R/OpenAction<</S/JavaScript/JS(app.alert(1))>>>>endobj\n")
    found = _interactive_content(raw)
    assert found == ["JavaScript", "Link annotation", "Widget annotation", "form fields"]
    check = check_interactive_content({"interactive": found}, {})
    assert check.status == "fail" and check.severity == "error"


def test_interactive_content_allows_printer_marks():
    """PDF/X permits PrinterMark and TrapNet annotations."""
    raw = _page(b"/Annots[<</Subtype/PrinterMark>> <</Subtype/TrapNet>>]")
    assert _interactive_content(raw) == []


def test_interactive_content_ignores_bytes_inside_streams():
    """Compressed stream data matched `/JS` by chance in a real book."""
    raw = _page() + b"2 0 obj<</Length 20>>stream\n\x9c/JS /AA /Subtype/Link\nendstream endobj\n"
    assert _interactive_content(raw) == []


def test_interactive_content_is_unmeasured_behind_object_streams():
    """Not clean: an /ObjStm hides the dictionaries a byte scan would read."""
    raw = _page() + b"2 0 obj<</Type/ObjStm/N 3/First 9/Length 4>>stream\nxxxx\nendstream endobj\n"
    assert _interactive_content(raw) is None
    assert check_interactive_content({"interactive": None}, {}).status == "warn"


# --- ink coverage and rich black -----------------------------------------

import subprocess

import pytest

from publisher_prepress.ghostscript import find_binary
from publisher_prepress.preflight import (check_ink_coverage, check_rich_black_text,
                                          measure_ink)

needs_gs = pytest.mark.skipif(find_binary() is None, reason="needs Ghostscript")

TEXT = "/Helvetica findfont 36 scalefont setfont 72 300 moveto (Rich) show"
BOX = "72 72 144 144 rectfill"


def _pdf(tmp_path, *pages):
    """A PDF from PostScript pages, so every colour is exactly what the test set."""
    ps = tmp_path / "in.ps"
    ps.write_text("%!\n" + "".join(f"{page}\nshowpage\n" for page in pages))
    pdf = tmp_path / "in.pdf"
    subprocess.run([find_binary(), "-q", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
                    f"-sOutputFile={pdf}", str(ps)], check=True)
    return pdf


@needs_gs
def test_rich_black_text_is_found_and_counted_as_ink(tmp_path):
    pdf = _pdf(tmp_path,
               f"0 0 0 1 setcmykcolor {TEXT}",
               f"0.72 0.67 0.67 0.88 setcmykcolor {TEXT}")
    ink = measure_ink(pdf)
    assert ink["rich_black_text_pages"] == [2]
    assert ink["tac"] == [100, 294]
    assert check_rich_black_text({"ink": ink}, {}).status == "fail"
    assert check_ink_coverage({"ink": ink}, {"pdfSpec": {"maxInkCoverage": 300}}).status == "pass"
    assert check_ink_coverage({"ink": ink}, {}).status == "skip"   # no limit stated


@needs_gs
def test_heavy_ink_art_fails_coverage_but_is_not_rich_black_text(tmp_path):
    """Only text is judged for rich black; a 400% box is an ink-coverage failure."""
    pdf = _pdf(tmp_path, f"1 1 1 1 setcmykcolor {BOX} 0 0 0 1 setcmykcolor {TEXT}")
    ink = measure_ink(pdf)
    assert ink == {"tac": [400], "rich_black_text_pages": []}
    check = check_ink_coverage({"ink": ink}, {"pdfSpec": {"maxInkCoverage": 300}})
    assert check.status == "fail" and check.value == 400 and check.expected == 300
    assert check_ink_coverage({"ink": ink}, {"pdfSpec": {"maxInkCoverage": 400}}).status == "pass"


def test_ink_checks_warn_when_nothing_rendered_the_pages():
    for check in (check_ink_coverage, check_rich_black_text):
        assert check({"ink": {"unmeasured": "Ghostscript is not installed"}}, {}).status == "warn"
        assert check({}, {}).status == "warn"


def test_dark_colour_text_is_not_rich_black():
    from publisher_prepress.preflight import is_rich_black
    assert is_rich_black((72, 67, 67, 88)) and is_rich_black((100, 100, 100, 100))
    assert not is_rich_black((0, 0, 0, 100))
    assert not is_rich_black((20, 100, 80, 50))   # a burgundy heading


# --- transparency -----------------------------------------------------------

from publisher_prepress.preflight import _transparency, check_transparency

X1A = {"pdfSpec": {"standard": "pdfx-1a"}}


def test_transparency_clean_file_and_opaque_settings_pass():
    """/SMask /None, alpha 1 and the Normal blend mode are all opaque."""
    raw = _page(b"/Resources<</ExtGState<</G0<</SMask/None/CA 1/ca 1.0/BM/Normal>>"
                b"/G1<</BM[/Compatible]>>>>>>")
    assert _transparency(raw) == []
    assert check_transparency({"transparency": []}, X1A).status == "pass"


def test_transparency_finds_every_kind_a_pdfx_1a_rip_rejects():
    raw = (_page(b"/Group<</S/Transparency/CS/DeviceRGB>>")
           + b"2 0 obj<</Type/ExtGState/ca 0.5/BM/Multiply>>endobj\n"
           + b"3 0 obj<</Type/XObject/Subtype/Image/SMask 4 0 R>>endobj\n")
    found = _transparency(raw)
    assert found == ["Multiply blend mode", "constant alpha below 1",
                     "soft masks", "transparency groups"]
    check = check_transparency({"transparency": found}, X1A)
    assert check.status == "fail" and check.severity == "error"


def test_transparency_ignores_bytes_inside_streams():
    raw = _page() + b"2 0 obj<</Length 30>>stream\n/SMask 9 0 R /ca 0.2 /BM/Screen\nendstream endobj\n"
    assert _transparency(raw) == []


def test_transparency_is_unmeasured_behind_object_streams():
    raw = _page() + b"2 0 obj<</Type/ObjStm/N 3>>stream\nx\nendstream endobj\n"
    assert _transparency(raw) is None
    assert check_transparency({"transparency": None}, X1A).status == "warn"


def test_transparency_is_gated_by_the_profiles_standard():
    """PDF/X-4 carries transparency live; a profile stating no standard is not
    assumed to forbid it."""
    found = {"transparency": ["soft masks"]}
    assert check_transparency(found, {"pdfSpec": {"standard": "pdfx-3"}}).status == "fail"
    assert check_transparency(found, {"pdfSpec": {"standard": "pdfx-4"}}).status == "skip"
    assert check_transparency(found, {}).status == "skip"
