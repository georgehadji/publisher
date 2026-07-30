"""Tests for preflight rule engine."""

import json
import tempfile
from pathlib import Path

from publisher_prepress.preflight import (
    PreflightCheck, PreflightReport, run_preflight,
    check_trim_size, check_bleed, check_min_pages, check_max_pages,
    check_file_size, check_color_space, check_page_multiple,
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
    profile = {"proofSpec": {"dpi": 150}}
    result = check_resolution(pdf_info, profile)
    assert result.status == "pass"


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
