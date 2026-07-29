/**
 * Content-addressed store types — TypeScript.
 *
 * From ARCHITECTURE.md §3.2:
 * - Sha256 is not "string" — it has its own type with invariants.
 * - ArtifactRef carries hash, media_type, and size.
 */

import { createHash } from "node:crypto";

/**
 * A SHA-256 hash value as a hex string.
 * Construction validates the hex representation.
 */
export class Sha256 {
  private readonly _hex: string;

  constructor(hex: string) {
    if (hex.length !== 64) {
      throw new Error(`Sha256 hex string must be 64 chars, got ${hex.length}`);
    }
    if (!/^[a-f0-9]{64}$/.test(hex)) {
      throw new Error(`Sha256 hex string contains non-hex characters: ${hex}`);
    }
    this._hex = hex;
  }

  static fromBytes(data: Uint8Array): Sha256 {
    const hash = createHash("sha256").update(data).digest("hex");
    return new Sha256(hash);
  }

  static fromString(data: string): Sha256 {
    return this.fromBytes(Buffer.from(data, "utf-8"));
  }

  toString(): string {
    return this._hex;
  }

  toJSON(): string {
    return this._hex;
  }

  equals(other: Sha256 | string): boolean {
    if (other instanceof Sha256) {
      return this._hex === other._hex;
    }
    return this._hex === other;
  }

  /** First 2 hex characters for sharded storage paths. */
  get prefix1(): string {
    return this._hex.slice(0, 2);
  }

  /** Next 2 hex characters for sharded storage paths. */
  get prefix2(): string {
    return this._hex.slice(2, 4);
  }
}

/** IANA media type (MIME type). */
export class MediaType {
  readonly value: string;

  constructor(value: string) {
    if (!value || !value.includes("/")) {
      throw new Error(`Invalid media type: ${value}`);
    }
    this.value = value;
  }

  static readonly APPLICATION_PDF = new MediaType("application/pdf");
  static readonly APPLICATION_JSON = new MediaType("application/json");
  static readonly APPLICATION_XML = new MediaType("application/xml");
  static readonly APPLICATION_EPUB = new MediaType("application/epub+zip");
  static readonly APPLICATION_IDML = new MediaType(
    "application/vnd.adobe.indesign-idml-package"
  );
  static readonly TEXT_HTML = new MediaType("text/html");
  static readonly TEXT_CSS = new MediaType("text/css");
  static readonly TEXT_PLAIN = new MediaType("text/plain");
  static readonly IMAGE_PNG = new MediaType("image/png");
  static readonly IMAGE_JPEG = new MediaType("image/jpeg");
  static readonly IMAGE_SVG = new MediaType("image/svg+xml");
  static readonly FONT_TTF = new MediaType("font/ttf");
  static readonly FONT_OTF = new MediaType("font/otf");
  static readonly OCTET_STREAM = new MediaType("application/octet-stream");

  toString(): string {
    return this.value;
  }
}

/** Reference to an immutable artifact in the content-addressed store. */
export interface ArtifactRef {
  hash: Sha256;
  mediaType: MediaType;
  size: number; // bytes
}

/** Configuration for the local content-addressed store. */
export interface CasConfig {
  localCacheRoot: string;
  maxMmapSize?: number; // bytes, default 8 MB
  multipartThreshold?: number; // bytes, default 64 MB
}

/**
 * Default CAS configuration for local-only development.
 */
export function defaultLocalConfig(cacheRoot?: string): CasConfig {
  return {
    localCacheRoot: cacheRoot ?? ".cas-cache",
    maxMmapSize: 8 * 1024 * 1024,
    multipartThreshold: 64 * 1024 * 1024,
  };
}

/**
 * Compute the sharded local path for a hash:
 * `<root>/<prefix[0:2]>/<prefix[2:4]>/<hash>`.
 */
export function shardedPath(root: string, hash: Sha256): string {
  return `${root}/${hash.prefix1}/${hash.prefix2}/${hash}`;
}

/**
 * Compute the cache key for a build stage.
 *
 * From ARCHITECTURE.md §2.5:
 * ```
 * cache_key(stage, version, inputs, params, toolchain) = sha256(canonicalJson({
 *   "stage": stage,
 *   "version": version,
 *   "inputs": sorted(inputs),
 *   "params": params,
 *   "toolchain": toolchain,
 * }))
 * ```
 */
export function computeCacheKey(
  stage: string,
  version: number,
  inputs: string[],
  params: Record<string, unknown> | null,
  toolchain: Record<string, string>
): Sha256 {
  const payload = {
    stage,
    version,
    inputs: [...inputs].sort(),
    params: params ?? {},
    toolchain,
  };

  // Canonical JSON: sorted keys, no whitespace
  const json = JSON.stringify(payload, Object.keys(payload).sort());
  return Sha256.fromString(json);
}
