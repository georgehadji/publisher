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
# Rules (ARCHITECTURE.md §2.5): sorted keys, fixed float formatting, drop nulls.
# "Params must be canonical... Otherwise you get cache misses that look like
# nondeterminism."
#
# The previous implementation claimed all three rules in this comment and implemented
# only sorted keys. Worse, it passed `default=str`, which silently stringifies any
# non-JSON object: for a class without __str__ that yields "<Obj at 0x7f...>" — a memory
# ADDRESS inside the cache key, so the same params produced a different key on every
# process. For a datetime it embedded a wall-clock instant, with the same effect.


def _canonicalize(obj):
    """Recursively apply the three canonicalisation rules."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj

    if isinstance(obj, float):
        # Fixed formatting: 1.0, 1, and 1.00 must not produce three different keys.
        # repr() round-trips exactly; normalise integral floats to int.
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError(f"non-finite float in cache params: {obj!r}")
        return int(obj) if obj.is_integer() else float(repr(obj))

    if isinstance(obj, dict):
        # Drop nulls: an absent key and an explicit null are the same input.
        return {k: _canonicalize(v) for k, v in sorted(obj.items()) if v is not None}

    if isinstance(obj, (list, tuple)):
        return [_canonicalize(v) for v in obj]

    if isinstance(obj, (set, frozenset)):
        return [_canonicalize(v) for v in sorted(obj)]

    # Refuse rather than stringify. An object with no JSON representation in the cache
    # key is a bug at the call site; silently encoding its repr() is how a memory
    # address ends up deciding cache identity.
    raise TypeError(
        f"cannot canonicalise {type(obj).__name__} for a cache key: {obj!r}. "
        f"Convert it to a JSON-native value at the call site."
    )


def canonical_json(obj) -> str:
    """Serialize a Python object to canonical JSON for cache key computation."""
    return json.dumps(
        _canonicalize(obj),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
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
