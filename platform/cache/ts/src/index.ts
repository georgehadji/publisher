/**
 * publisher/cache — Build-graph memoization
 *
 * Map cache_key → artifact_refs. Decide hit/miss.
 * From BUILD_PLAN.md §3.3 and ARCHITECTURE.md §2.5.
 */

import { createHash } from "node:crypto";

/** Canonical JSON encoding for cache key computation. */
export function canonicalJson(obj: unknown): string {
  return JSON.stringify(obj, sortKeys, 0);
}

function sortKeys(_key: string, value: unknown): unknown {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[k] = (value as Record<string, unknown>)[k];
    }
    return sorted;
  }
  return value;
}

/**
 * Compute a cache key from stage inputs.
 * From ARCHITECTURE.md §2.5.
 */
export function computeCacheKey(
  stage: string,
  version: number,
  inputs: string[],
  params: Record<string, unknown>,
  toolchain: Record<string, unknown>
): string {
  const payload = {
    stage,
    version,
    inputs: [...inputs].sort(),
    params,
    toolchain,
  };

  return createHash("sha256")
    .update(canonicalJson(payload))
    .digest("hex");
}

/**
 * Compute the toolchain digest — part of every cache key.
 * Includes learned state (D10).
 */
export function computeToolchainDigest(
  imageDigests: Record<string, string>,
  fontsetHash: string,
  iccHashes: Record<string, string>,
  hyphenDictVersions: Record<string, string>,
  engineSemvers: Record<string, string>,
  extra: Record<string, string> = {}
): string {
  const payload = {
    images: imageDigests,
    fonts: fontsetHash,
    icc: iccHashes,
    hyphen: hyphenDictVersions,
    engines: engineSemvers,
    ...extra,
  };

  return createHash("sha256")
    .update(canonicalJson(payload))
    .digest("hex");
}

/** Cache entry stored in the index. */
export interface CacheEntry {
  cacheKey: string;
  stage: string;
  version: number;
  inputs: string[];
  paramsHash: string;
  toolchainHash: string;
  outputRefs: Record<string, string>; // artifact_kind → sha256
  createdAt: Date;
  hitCount: number;
  byteCount: number;
  ttlSeconds?: number;
}
