"""
Profile loader — versioned vendor specs as data, not code.

Reads YAML profile files from profiles/ and returns typed OutputProfile objects.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml  # type: ignore


_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def load_profile(name: str) -> Optional[dict]:
    """
    Load a vendor profile by name.
    
    Scans all profiles/ subdirectories for YAML files containing the named profile.
    YAML files can be multi-document (--- separated) for multiple profiles per file.
    """
    profiles_dir = _PROFILES_DIR
    if not profiles_dir.exists():
        # Try relative to CWD
        profiles_dir = Path("profiles")
    
    for vendor_dir in profiles_dir.iterdir():
        if not vendor_dir.is_dir():
            continue
        for yaml_file in vendor_dir.glob("*.yaml"):
            docs = list(yaml.safe_load_all(yaml_file.read_text()))
            for doc in docs:
                if doc and doc.get("name") == name:
                    return doc
    return None


def list_profiles(vendor: Optional[str] = None) -> list[dict]:
    """List all available profiles, optionally filtered by vendor."""
    profiles_dir = _PROFILES_DIR
    if not profiles_dir.exists():
        profiles_dir = Path("profiles")
    
    results = []
    for vendor_dir in profiles_dir.iterdir():
        if not vendor_dir.is_dir():
            continue
        if vendor and vendor_dir.name != vendor:
            continue
        for yaml_file in vendor_dir.glob("*.yaml"):
            docs = list(yaml.safe_load_all(yaml_file.read_text()))
            results.extend(d for d in docs if d)
    
    return results


def get_default_profile() -> dict:
    """Return the generic default profile for testing."""
    return load_profile("Generic 6x9") or {
        "schema": "profile/1",
        "name": "Default",
        "vendor": "generic",
        "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
        "pdfSpec": {"version": "1.7", "standard": "pdfx-1a", "colorSpace": "cmyk"},
        "minPages": 1,
        "maxPages": 2000,
        "pageSizeMultiple": 1,
    }
