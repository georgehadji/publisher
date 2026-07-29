"""
publisher_cache -- Build-graph memoization

Map cache_key -> artifact_refs. Decide hit/miss. GC.
From BUILD_PLAN.md §3.3 and ARCHITECTURE.md §2.5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class CacheEntry:
    """A single cache entry mapping a key to its output artifacts."""
    cache_key: str
    stage: str
    version: int
    inputs: list[str]  # sorted sha256 hashes of input artifacts
    params_hash: str   # sha256 of canonical JSON params
    toolchain_hash: str  # sha256 of toolchain digest
    output_refs: dict[str, str]  # artifact_kind -> sha256
    created_at: datetime
    hit_count: int = 0
    byte_count: int = 0
    ttl_seconds: Optional[int] = None


# Canonical JSON encoding for cache key computation.
# Rules: sorted keys, fixed float formatting, drop nulls.
def canonical_json(obj) -> str:
    """Serialize a Python object to canonical JSON for cache key computation."""
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def compute_cache_key(
    stage: str,
    version: int,
    inputs: list[str],
    params: dict,
    toolchain: dict,
) -> str:
    """
    Compute a cache key from stage inputs.

    From ARCHITECTURE.md §2.5:
    ```
    sha256(canonical_json({
        "stage": stage,
        "version": version,
        "inputs": sorted(inputs),
        "params": params,
        "toolchain": toolchain,
    }))
    ```
    """
    import hashlib

    payload = {
        "stage": stage,
        "version": version,
        "inputs": sorted(inputs),
        "params": params,
        "toolchain": toolchain,
    }

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_toolchain_digest(
    image_digests: dict[str, str],
    fontset_hash: str,
    icc_hashes: dict[str, str],
    hyphen_dict_versions: dict[str, str],
    engine_semvers: dict[str, str],
    **extra: str,
) -> str:
    """
    Compute the toolchain digest -- part of every cache key.

    Includes learned state (D10): image digests, fontset, ICC, hyphen dicts,
    engine semvers, exemplar set hash, rule set version, prompt version, model id.
    """
    import hashlib

    payload = {
        "images": image_digests,
        "fonts": fontset_hash,
        "icc": icc_hashes,
        "hyphen": hyphen_dict_versions,
        "engines": engine_semvers,
    }
    payload.update(extra)

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
