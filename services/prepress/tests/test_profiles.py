"""Tests for profile loading."""

from profiles import load_profile, list_profiles, get_default_profile


def test_load_kdp_profile():
    profile = load_profile("KDP US Trade 6x9")
    assert profile is not None
    assert profile["vendor"] == "kdp"
    assert profile["trimSize"]["width"] == 152.4
    assert profile["pdfSpec"]["colorSpace"] == "cmyk"


def test_load_ingramspark_profile():
    profile = load_profile("IngramSpark US Trade 6x9")
    assert profile is not None
    assert profile["vendor"] == "ingramspark"
    assert profile["minPages"] == 20


def test_load_generic_profile():
    profile = load_profile("Generic 6x9")
    assert profile is not None
    assert profile["vendor"] == "generic"


def test_load_nonexistent_profile():
    profile = load_profile("Nonexistent Profile Name")
    assert profile is None


def test_list_profiles():
    profiles = list_profiles()
    assert len(profiles) >= 1


def test_list_profiles_by_vendor():
    kdp_profiles = list_profiles(vendor="kdp")
    assert len(kdp_profiles) >= 1
    for p in kdp_profiles:
        assert p["vendor"] == "kdp"


def test_get_default_profile():
    profile = get_default_profile()
    assert profile is not None
    assert "trimSize" in profile
