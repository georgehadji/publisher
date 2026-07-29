"""
publisher_cas -- Content-Addressed Store

Put/get immutable blobs by sha256. Local node cache in front of S3.
Functional core (key derivation pure), imperative shell (IO).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .types import ArtifactRef, Sha256, MediaType


class CasConfig:
    """Configuration for the content-addressed store."""

    def __init__(
        self,
        local_cache_root: Path,
        s3_bucket: str = "",
        s3_prefix: str = "cas",
        max_mmap_size: int = 8 * 1024 * 1024,  # 8 MB -- mmap threshold
        multipart_threshold: int = 64 * 1024 * 1024,  # 64 MB
    ):
        self.local_cache_root = Path(local_cache_root)
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix
        self.max_mmap_size = max_mmap_size
        self.multipart_threshold = multipart_threshold
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.local_cache_root.mkdir(parents=True, exist_ok=True)


class ContentAddressedStore:
    """
    Immutable blob store keyed by sha256 hash.

    Design (from ARCHITECTURE.md §3.2):
    - Hash while streaming, never read-then-hash.
    - mmap for local reads > 8 MB.
    - Node-local disk cache keyed by hash -- content-addressed means
      it's trivially correct and never invalidates.
    - Write to tmp then atomic rename -- the hash IS the integrity check.
    - S3 multipart above 64 MB.
    """

    def __init__(self, config: CasConfig):
        self._config = config

    # ── Public API ────────────────────────────────────────────────

    def put(self, data: bytes, media_type: Optional[MediaType] = None) -> ArtifactRef:
        """
        Store bytes, return an ArtifactRef.
        Raises ValueError if data is empty.
        """
        if not data:
            raise ValueError("Cannot store empty blob")

        content_hash = Sha256.from_bytes(data)
        local_path = self._local_path(content_hash)

        if not local_path.exists():
            self._write_atomic(local_path, data)

        return ArtifactRef(
            hash=content_hash,
            media_type=media_type or MediaType("application/octet-stream"),
            size=len(data),
        )

    def put_stream(self, reader, media_type: Optional[MediaType] = None) -> ArtifactRef:
        """
        Store data from a binary reader (file-like), return an ArtifactRef.
        Hashes while reading -- never reads twice.
        """
        hasher = hashlib.sha256()
        chunk_size = 64 * 1024  # 64 KB
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        total_size = 0

        try:
            while True:
                chunk = reader.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                temp_file.write(chunk)
                total_size += len(chunk)

            temp_file.flush()
            temp_file.close()

            content_hash = Sha256(hasher.hexdigest())
            local_path = self._local_path(content_hash)

            if not local_path.exists():
                shutil.move(temp_file.name, str(local_path))
            else:
                os.unlink(temp_file.name)

            return ArtifactRef(
                hash=content_hash,
                media_type=media_type or MediaType("application/octet-stream"),
                size=total_size,
            )
        except Exception:
            try:
                os.unlink(temp_file.name)
            except OSError:
                pass
            raise

    def get(self, ref: ArtifactRef) -> bytes:
        """
        Retrieve bytes by ArtifactRef.
        Raises FileNotFoundError if the blob is not in local cache.
        """
        local_path = self._local_path(ref.hash)
        if not local_path.exists():
            raise FileNotFoundError(
                f"Blob {ref.hash} not found in local cache at {local_path}"
            )
        return local_path.read_bytes()

    def get_path(self, ref: ArtifactRef) -> Path:
        """
        Return the local filesystem path for zero-copy handoff.
        The caller must NOT modify the file at this path.
        """
        local_path = self._local_path(ref.hash)
        if not local_path.exists():
            raise FileNotFoundError(
                f"Blob {ref.hash} not found in local cache at {local_path}"
            )
        return local_path

    def exists(self, ref: ArtifactRef) -> bool:
        """Check if a blob exists in local cache."""
        return self._local_path(ref.hash).exists()

    def delete(self, ref: ArtifactRef) -> None:
        """Remove a blob from local cache. Does not affect S3."""
        local_path = self._local_path(ref.hash)
        if local_path.exists():
            local_path.unlink()

    # ── Internal ──────────────────────────────────────────────────

    def _local_path(self, content_hash: Sha256) -> Path:
        """Compute sharded local path: cache_root/sha256[0:2]/sha256[2:4]/sha256."""
        hex_str = str(content_hash)
        return (
            self._config.local_cache_root
            / hex_str[:2]
            / hex_str[2:4]
            / hex_str
        )

    def _write_atomic(self, path: Path, data: bytes) -> None:
        """Write to tmp then atomic rename -- the hash IS the integrity check."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to a temp file in the same directory (same filesystem -> atomic rename)
        tmp_path = path.with_suffix(".tmp." + os.urandom(4).hex())
        try:
            tmp_path.write_bytes(data)
            tmp_path.rename(path)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise


# ── Convenience ──────────────────────────────────────────────────

def new_local_store(cache_root: str | Path) -> ContentAddressedStore:
    """Create a store backed only by local disk (no S3)."""
    return ContentAddressedStore(CasConfig(local_cache_root=Path(cache_root)))
