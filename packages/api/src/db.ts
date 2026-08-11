/**
 * Repository + infrastructure module (U6) -- the API's single data-access layer.
 *
 * Everything that touches Postgres or the CAS filesystem lives here so routes
 * stay thin adapters. The tenant-ownership check is centralized in `loadOwned`
 * (a table whitelist, never interpolated user input) -- the U6 motivation: the
 * one place that check was originally missing (manuscript creation) is exactly
 * the omission a single helper makes structurally impossible.
 *
 * Also carries the U5 hardening that belongs with the data layer:
 *   S2  casPath validates ^[a-f0-9]{64}$ before path.join (N5)
 *   S6  readCasFile refuses files over a configured ceiling (N4)
 *   S8  pg Pool is configured (max / idle / connection timeouts), not defaults
 */
import path from 'node:path';
import { access, readFile, stat } from 'node:fs/promises';
import pg from 'pg';

const { Pool } = pg;

export const CAS_ROOT = process.env.PUBLISHER_CAS_ROOT ?? './.publisher/cas';

// S8 -- the pg Pool was `new Pool({ connectionString })` with library defaults
// (max 10, no idle/connection timeouts). With the SSE LISTEN/NOTIFY fix (S11)
// the pool is no longer hammered by polls, but a burst of concurrent uploads or
// SSE streams still needs a sane ceiling and must not hand out dead connections.
export const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: parseInt(process.env.PUBLISHER_PG_POOL_MAX ?? '20', 10),
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
});

export const UPLOAD_MAX_BYTES = parseInt(process.env.PUBLISHER_UPLOAD_MAX_BYTES ?? '104857600', 10);

// S6 -- the ceiling for whole-file CAS reads (ast/1, preflight/1). A 900-page
// manuscript's AST is well under this; anything over it is refused loudly
// rather than loaded into the API process unbounded (N4). Streaming + an
// incremental JSON parse above this ceiling is future work -- no stdlib
// incremental parser exists, and no corpus reaches the ceiling today.
export const CAS_READ_MAX_BYTES = parseInt(process.env.PUBLISHER_CAS_READ_MAX_BYTES ?? '67108864', 10);

const SHA256_RE = /^[a-f0-9]{64}$/;

/** S2 -- the CAS path for a sha256. Refuses malformed hashes before path.join
 * (N5): the value usually comes from the artifacts table, but one bad INSERT
 * away from being a path traversal is exactly the class this kills. */
export function casPath(sha256: string): string {
  if (!SHA256_RE.test(sha256)) {
    throw new TypeError(`casPath: malformed sha256 ${JSON.stringify(sha256)}`);
  }
  return path.join(CAS_ROOT, sha256.slice(0, 2), sha256.slice(2, 4), sha256);
}

export class CasReadTooLargeError extends Error {
  constructor(public readonly sha256: string, public readonly size: number, public readonly ceiling: number) {
    super(`CAS blob ${sha256} is ${size} bytes, over the ${ceiling} byte read ceiling`);
  }
}

/** S6 -- read a CAS blob as UTF-8, refusing anything over CAS_READ_MAX_BYTES. */
export async function readCasFile(sha256: string): Promise<string> {
  const blob = casPath(sha256);
  const st = await stat(blob);
  if (st.size > CAS_READ_MAX_BYTES) {
    throw new CasReadTooLargeError(sha256, st.size, CAS_READ_MAX_BYTES);
  }
  return readFile(blob, 'utf-8');
}

export async function casBlobExists(sha256: string): Promise<boolean> {
  try {
    await access(casPath(sha256));
    return true;
  } catch {
    return false;
  }
}

// The public artifact vocabulary, mapped to the schema ID that actually
// identifies the bytes.
//
// A stage's `kind` is unique only inside that stage's own outputs={} dict:
// paginate emits kind='pdf' (raw-pdf/1) and finish emits kind='pdf' (pdfx/1);
// paginate, finish and preflight all emit kind='report'. Selecting on `kind`
// therefore returned whichever row happened to be there -- GET .../artifacts/pdf
// served paginate's UNCONVERTED weasyprint PDF as the press file, and
// GET .../preflight returned finish's report instead of the preflight verdict.
//
// This map is the API's delivery contract: `pdf` means the press-ready PDF/X,
// never the raw render. Every schema below is asserted against the real stage
// registry by tests/integration/test_api_drives_pipeline.py, so a rename in a
// stage declaration fails a test rather than silently 404ing in production.
export const DELIVERABLE_SCHEMAS: Readonly<Record<string, string>> = Object.freeze({
  pdf: 'pdfx/1',
  proof: 'proof-pdf/1',
  'raw-pdf': 'raw-pdf/1',
  preflight: 'preflight/1',
  pagemap: 'pagemap/1',
  ast: 'ast/1',
  doc: 'doc-effective/1',
  html: 'typescript-html/1',
  css: 'text/css',
  'build-report': 'build-report/1',
  'integrity-report': 'integrity-report/1',
});

const OWNED_TABLES = ['titles', 'manuscripts', 'builds', 'webhooks'] as const;
export type OwnedTable = (typeof OWNED_TABLES)[number];

/** Load a row by id AND require it to belong to the caller's tenant.
 *
 * 404-not-403 is deliberate: another tenant's resource must be
 * indistinguishable from a nonexistent one. The table name comes from a fixed
 * whitelist, so the interpolation below is not injectable.
 */
export async function loadOwned<T = any>(table: OwnedTable, id: string, tenantId: string): Promise<T | null> {
  const row = (await pool.query(`SELECT * FROM ${table} WHERE id = $1`, [id])).rows[0];
  if (!row || row.tenant_id !== tenantId) return null;
  return row;
}
