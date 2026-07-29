"""
Core types for the content-addressed store.

From ARCHITECTURE.md §3.2:
- Sha256 is not "str" -- it has its own type with invariants.
- ArtifactRef carries hash, media_type, and size.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Union


class Sha256:
    """
    A SHA-256 hash value as a hex string.
    Parse, don't validate -- construction validates the hex representation.
    """

    def __init__(self, hex_str: str):
        if not isinstance(hex_str, str):
            raise TypeError(f"Sha256 requires a hex string, got {type(hex_str)}")
        if len(hex_str) != 64:
            raise ValueError(f"Sha256 hex string must be 64 chars, got {len(hex_str)}")
        try:
            int(hex_str, 16)
        except ValueError:
            raise ValueError(f"Sha256 hex string contains non-hex characters: {hex_str}")
        self._hex = hex_str

    @classmethod
    def from_bytes(cls, data: bytes) -> "Sha256":
        """Compute the SHA-256 hash of bytes."""
        return cls(hashlib.sha256(data).hexdigest())

    @classmethod
    def from_file(cls, path: str) -> "Sha256":
        """Compute the SHA-256 hash of a file's contents."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return cls(h.hexdigest())

    def __str__(self) -> str:
        return self._hex

    def __repr__(self) -> str:
        return f"Sha256({self._hex[:16]}…)"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sha256):
            return self._hex == other._hex
        if isinstance(other, str):
            return self._hex == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._hex)

    def __lt__(self, other: "Sha256") -> bool:
        return self._hex < other._hex


@dataclass(frozen=True)
class MediaType:
    """An IANA media type (MIME type)."""

    value: str

    def __post_init__(self):
        if not self.value or "/" not in self.value:
            raise ValueError(f"Invalid media type: {self.value}")

    APPLICATION_PDF = "application/pdf"
    APPLICATION_JSON = "application/json"
    APPLICATION_XML = "application/xml"
    APPLICATION_EPUB = "application/epub+zip"
    APPLICATION_IDML = "application/vnd.adobe.indesign-idml-package"
    TEXT_HTML = "text/html"
    TEXT_CSS = "text/css"
    TEXT_PLAIN = "text/plain"
    IMAGE_PNG = "image/png"
    IMAGE_JPEG = "image/jpeg"
    IMAGE_SVG = "image/svg+xml"
    IMAGE_TIFF = "image/tiff"
    FONT_TTF = "font/ttf"
    FONT_OTF = "font/otf"
    FONT_WOFF2 = "font/woff2"
    OCTET_STREAM = "application/octet-stream"


@dataclass(frozen=True)
class ArtifactRef:
    """
    Reference to an immutable artifact in the content-addressed store.
    """

    hash: Sha256
    media_type: MediaType
    size: int  # bytes

    def __post_init__(self):
        if self.size < 0:
            raise ValueError(f"size must be non-negative, got {self.size}")


# Allow Union type shorthand
Sha256OrStr = Union[Sha256, str]
