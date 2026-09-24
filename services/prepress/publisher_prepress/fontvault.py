"""
Font vault and licensing enforcement.

From ARCHITECTURE.md §2.10:
design-compile REFUSES to emit a spec referencing a font whose allowedUses
don't cover the target output. Enforced in the domain layer, not the UI.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
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


# The house faces (2026-09-24). GFS Didot is OFL and ships in the worker image
# (Debian `fonts-gfs-didot`); it is the default because it is the one every
# worker can have. Minion Pro and PN Katsoulidis are commercial: licensed for
# print PDF embedding (their fsType allows it), never EPUB embedding or
# redistribution, and supplied by a deployment through PUBLISHER_FONT_DIRS --
# they are not, and must not be, in this public repository.
# All three cover the first real manuscript's 120 polytonic Greek characters.
for family, source, license_ref, uses in (
    ("GFS Didot", "bundled_ofl", "OFL-1.1", ["PRINT_PDF", "EPUB_EMBED", "SERVER_RENDER"]),
    ("Minion Pro", "licensed_server", "Adobe-Font-EULA", ["PRINT_PDF"]),
    ("PN Katsoulidis", "licensed_server", "fonts.gr-EULA", ["PRINT_PDF"]),
):
    for style in ("regular", "italic", "bold", "bold-italic"):
        register_font(FontAsset(
            family=family, style=style,
            hash=_identity_hash(family, style, source, license_ref),
            source=source, licenseRef=license_ref, allowedUses=uses,
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


# ── Font files: where a vault face actually lives on this machine ─────────
#
# The vault above says a face is LICENSED; nothing used to say it was PRESENT.
# The renderer was handed `font-family: EB Garamond`, found no such font on any
# machine this project ran on, and silently substituted whatever it had -- so no
# book was ever set in its designed face, and nothing noticed. `font_faces`
# resolves each family to files and the renderer embeds exactly those; a family
# with no regular face on disk is reported, so the caller can refuse to render.

FONT_DIRS_ENV = "PUBLISHER_FONT_DIRS"
_FONT_SUFFIXES = (".otf", ".ttf")
# (css font-weight, css font-style) per vault style.
_FACE_CSS = {
    "regular": (400, "normal"),
    "italic": (400, "italic"),
    "bold": (700, "normal"),
    "bold-italic": (700, "italic"),
}


def font_dirs() -> tuple[str, ...]:
    """Directories searched for font files, in priority order.

    PUBLISHER_FONT_DIRS (os.pathsep-separated) first -- where a deployment puts
    its licensed faces -- then the platform's system and per-user font folders.
    The per-user folder matters: on Windows a font installed "for me" lands in
    %LOCALAPPDATA%, which fontconfig does not scan.
    """
    import os
    import sys

    dirs = [d for d in os.environ.get(FONT_DIRS_ENV, "").split(os.pathsep) if d]
    home = Path.home()
    if sys.platform == "win32":
        dirs += [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        dirs += ["/Library/Fonts", "/System/Library/Fonts", str(home / "Library" / "Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts",
                 str(home / ".local" / "share" / "fonts"), str(home / ".fonts")]
    return tuple(dirs)


def _style_of(font) -> Optional[str]:
    """The vault style of a font file, or None for a face the vault has no slot
    for (Medium, Semibold, Condensed...): regular/bold are weights 400/700 at
    normal width, italic is the fsSelection italic bit."""
    os2 = font["OS/2"]
    if os2.usWidthClass != 5 or os2.usWeightClass not in (400, 700):
        return None
    bold = os2.usWeightClass == 700
    italic = bool(os2.fsSelection & 1)
    return {(False, False): "regular", (False, True): "italic",
            (True, False): "bold", (True, True): "bold-italic"}[(bold, italic)]


@lru_cache(maxsize=8)
def _font_index(dirs: tuple[str, ...]) -> dict[tuple[str, str], Path]:
    """(family, style) -> file, over every font file under `dirs`.

    The first directory to supply a face wins, and files are visited in sorted
    order within a directory, so the answer does not depend on filesystem order.
    ponytail: a full scan (~1 s per thousand files), cached per process; key it
    on directory mtimes if fonts are installed while a worker runs.
    """
    from fontTools.ttLib import TTFont, TTLibError

    index: dict[tuple[str, str], Path] = {}
    for directory in dirs:
        root = Path(directory)
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.rglob("*") if p.suffix.lower() in _FONT_SUFFIXES):
            try:
                font = TTFont(str(path), lazy=True)
                name = font["name"]
                # Name ID 1, not the typographic family (16): for the four
                # RIBBI styles indexed here ID 1 IS the family, and ID 16 is
                # optional metadata a font can get wrong -- GFS Artemisia's bold
                # italic ships with ID 16 "GFS Didot", and won GFS Didot's slot.
                family = str(name.getDebugName(1) or "")
                style = _style_of(font)
            except (TTLibError, KeyError, OSError, ValueError, AssertionError):
                continue
            if family and style:
                index.setdefault((family, style), path)
    return index


def locate_font(family: str, style: str = "regular") -> Optional[Path]:
    return _font_index(font_dirs()).get((family, style))


def font_faces(families: list[str]) -> tuple[str, list[str]]:
    """`@font-face` rules pinning each family to its files, and the families
    that could not be found (no regular face on disk).

    The rules name files by absolute `file://` URL, so the renderer embeds what
    the vault resolved rather than whatever a font service would pick.
    """
    rules: list[str] = []
    missing: list[str] = []
    for family in dict.fromkeys(families):
        if locate_font(family, "regular") is None:
            missing.append(family)
            continue
        for style, (weight, css_style) in _FACE_CSS.items():
            path = locate_font(family, style)
            if path is not None:
                rules.append(
                    f'@font-face {{ font-family: "{family}"; src: url("{path.resolve().as_uri()}"); '
                    f"font-weight: {weight}; font-style: {css_style}; }}"
                )
    return "\n".join(rules), missing
