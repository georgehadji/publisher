"""Tests for font vault."""

import json
from publisher_prepress.fontvault import (
    FontAsset, FontLicenseViolation, FontLicenseManifest,
    get_font, register_font, validate_font_use, build_font_manifest,
)


def test_get_bundled_font():
    font = get_font("EB Garamond", "regular")
    assert font is not None
    assert font.family == "EB Garamond"
    assert font.source == "bundled_ofl"
    assert font.licenseRef == "OFL-1.1"
    assert "PRINT_PDF" in font.allowedUses


def test_get_missing_font():
    font = get_font("Nonexistent Font")
    assert font is None


def test_validate_font_use_pass():
    validate_font_use("EB Garamond", "regular", "PRINT_PDF")  # should not raise


def test_validate_font_use_missing():
    try:
        validate_font_use("Nonexistent", "regular", "PRINT_PDF")
        assert False, "Should have raised"
    except FontLicenseViolation:
        pass


def test_register_custom_font():
    font = FontAsset(
        family="CustomFont",
        style="regular",
        hash="abc123",
        source="tenant_upload",
        licenseRef="custom-license",
        allowedUses=["PRINT_PDF"],
        attestationBy="user-1",
        attestationAt="2025-01-01T00:00:00Z",
    )
    register_font(font)
    retrieved = get_font("CustomFont", "regular")
    assert retrieved is not None
    assert retrieved.hash == "abc123"


def test_build_font_manifest():
    manifest = build_font_manifest(
        [("EB Garamond", "regular"), ("Source Serif Pro", "bold")],
        build_id="test-001",
    )
    assert len(manifest.fonts) == 2
    assert manifest.buildId == "test-001"
    assert manifest.fonts[0].family == "EB Garamond"


def test_build_font_manifest_missing():
    try:
        build_font_manifest([("Nonexistent", "regular")], build_id="test")
        assert False, "Should have raised"
    except FontLicenseViolation:
        pass


def test_font_manifest_serialization():
    manifest = build_font_manifest([("EB Garamond", "regular")], build_id="test-002")
    d = manifest.to_dict()
    assert d["schema"] == "font-manifest/1"
    assert len(d["fonts"]) == 1
    assert d["fonts"][0]["family"] == "EB Garamond"
