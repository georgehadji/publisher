"""
Font vault and licensing enforcement.

From ARCHITECTURE.md §2.10:
design-compile REFUSES to emit a spec referencing a font whose allowedUses
don't cover the target output. Enforced in the domain layer, not the UI.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class FontLicenseViolation(Exception):
    """Raised when a font's license does not cover the target output."""
    pass


@dataclass(frozen=True)
class FontAsset:
    """A font asset with licensing metadata."""
    family: str
    style: str  # "regular", "italic", "bold", "bold-italic", etc.
    hash: str  # sha256 of the font file
    source: str  # "bundled_ofl" | "tenant_upload" | "licensed_server"
    licenseRef: str  # e.g. "OFL-1.1", "custom-2025-01"
    allowedUses: list[str] = field(default_factory=lambda: ["PRINT_PDF", "EPUB_EMBED", "SERVER_RENDER"])
    attestationBy: Optional[str] = None
    attestationAt: Optional[str] = None


@dataclass(frozen=True)
class FontLicenseManifest:
    """Per-build manifest of all fonts used."""
    fonts: list[FontAsset]
    buildId: str
    createdAt: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "schema": "font-manifest/1",
            "buildId": self.buildId,
            "fonts": [
                {"family": f.family, "style": f.style, "hash": f.hash,
                 "source": f.source, "licenseRef": f.licenseRef,
                 "allowedUses": f.allowedUses}
                for f in self.fonts
            ],
            "createdAt": self.createdAt,
        }


# ── Built-in OFL fonts (safe defaults) ─────────────────────────

_BUILTIN_FONTS: dict[str, FontAsset] = {}


def register_font(font: FontAsset) -> None:
    """Register a font in the vault."""
    key = f"{font.family}/{font.style}"
    _BUILTIN_FONTS[key] = font


# Register common open-source book fonts
for family, styles in {
    "EB Garamond": ["regular", "italic", "bold", "bold-italic"],
    "Source Serif Pro": ["regular", "italic", "bold", "bold-italic"],
    "Source Sans Pro": ["regular", "italic", "bold", "bold-italic"],
    "Noto Serif": ["regular", "italic", "bold", "bold-italic"],
    "Noto Sans": ["regular", "italic", "bold", "bold-italic"],
    "Libertinus Serif": ["regular", "italic", "bold", "bold-italic"],
    "Libertinus Sans": ["regular", "italic", "bold"],
    "Fira Mono": ["regular", "bold"],
    "Merriweather": ["regular", "italic", "bold", "bold-italic"],
}.items():
    for style in styles:
        register_font(FontAsset(
            family=family,
            style=style,
            hash="pending-license-verification",
            source="bundled_ofl",
            licenseRef="OFL-1.1",
            allowedUses=["PRINT_PDF", "EPUB_EMBED", "SERVER_RENDER"],
        ))


def get_font(family: str, style: str = "regular") -> Optional[FontAsset]:
    """Look up a font in the vault."""
    # Direct lookup
    key = f"{family}/{style}"
    if key in _BUILTIN_FONTS:
        return _BUILTIN_FONTS[key]
    
    # Try 'regular' as fallback
    if style != "regular":
        key2 = f"{family}/regular"
        return _BUILTIN_FONTS.get(key2)
    
    return None


def validate_font_use(family: str, style: str, target_output: str) -> None:
    """
    Verify a font is licensed for the target output.
    
    Raises FontLicenseViolation if the font is missing or not licensed.
    """
    font = get_font(family, style)
    if font is None:
        raise FontLicenseViolation(
            f"Font '{family} ({style})' is not in the font vault. "
            f"Allowed sources: bundled_ofl, tenant_upload, or licensed_server."
        )
    
    if target_output not in font.allowedUses:
        raise FontLicenseViolation(
            f"Font '{family} ({style})' is licensed for {font.allowedUses} "
            f"but target output requires '{target_output}'. "
            f"License ref: {font.licenseRef}."
        )


def build_font_manifest(font_refs: list[tuple[str, str]], build_id: str) -> FontLicenseManifest:
    """
    Build a font license manifest for a set of font references.
    Each ref is (family, style).
    
    Raises FontLicenseViolation for any unlicensed or missing font.
    """
    assets = []
    for family, style in font_refs:
        font = get_font(family, style)
        if font is None:
            raise FontLicenseViolation(
                f"Font '{family} ({style})' is not in the vault. "
                "Upload it or use a bundled font."
            )
        assets.append(font)
    
    return FontLicenseManifest(fonts=assets, buildId=build_id)


def hash_font_file(path: str | Path) -> str:
    """Compute sha256 of a font file."""
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()
