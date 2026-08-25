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


def _identity_hash(family: str, style: str, source: str, license_ref: str) -> str:
    """Stable per-face identity. NOT a file content hash — see the register_font loop."""
    digest = hashlib.sha256(f"{family}/{style}/{source}/{license_ref}".encode("utf-8")).hexdigest()
    return f"unverified:{digest}"


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
            # ponytail: identity hash derived from the face's metadata, not its bytes —
            # the OFL files are not vendored yet, so there is nothing to hash. All 34
            # faces previously shared the literal string "pending-license-verification",
            # which made the build manifest's fontset hash (§2.11) CONSTANT: changing
            # which fonts a build used never invalidated the cache. A derived identity
            # at least varies per face. Upgrade to hash_font_file() once the font files
            # are vendored; the `unverified:` prefix makes it impossible to mistake this
            # for a content hash in the meantime.
            hash=_identity_hash(family, style, "bundled_ofl", "OFL-1.1"),
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


# ── Tenant-uploaded faces ──────────────────────────────────────

def font_root() -> Path:
    """Directory holding tenant-uploaded font files.

    Kept out of the repository on purpose. Licensed commercial faces are the
    normal case for a real book, and committing them would redistribute them to
    everyone with a clone -- exactly what their licence forbids. `.publisher/`
    is already the local, git-ignored artifact area.
    """
    import os
    return Path(os.environ.get("PUBLISHER_FONT_ROOT", ".publisher/fonts"))


def register_tenant_font(
    family: str,
    style: str,
    file_name: str,
    license_ref: str,
    attested_by: str | None = None,
) -> FontAsset:
    """Register an uploaded face, hashing the actual file.

    Unlike the bundled OFL entries above, this one gets a REAL content hash --
    the file is present, so there is no reason to fall back to a hash of its
    metadata. That makes the build manifest's fontset hash change when the
    uploaded file changes, which is the property the cache key needs.

    Raises FontLicenseViolation when the file is missing, rather than
    registering a face the renderer will then silently substitute for.
    """
    path = font_root() / file_name
    if not path.is_file():
        raise FontLicenseViolation(
            f"Font '{family} ({style})' declares file '{file_name}', which is not "
            f"present under {font_root()}. Upload the face or correct the DesignSpec."
        )
    asset = FontAsset(
        family=family,
        style=style,
        hash=hash_font_file(path),
        source="tenant_upload",
        licenseRef=license_ref,
        allowedUses=["PRINT_PDF", "EPUB_EMBED", "SERVER_RENDER"],
        attestationBy=attested_by,
        attestationAt=datetime.now(timezone.utc).isoformat() if attested_by else None,
    )
    register_font(asset)
    return asset
