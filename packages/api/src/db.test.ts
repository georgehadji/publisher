/**
 * E0.4 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- unit tests for the CAS
 * hardening in db.ts: N5 (path traversal via a malformed hash) and N4 (a
 * bounded read, not an unbounded one) closing L3's "zero test files".
 * No DB: these only touch the filesystem.
 */
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { casPath } from './db.js';

const VALID_SHA = 'a'.repeat(64);

describe('casPath', () => {
  it('shards a valid sha256 into CAS_ROOT/aa/bb/<sha>', () => {
    const p = casPath(VALID_SHA);
    expect(p.endsWith(path.join('aa', 'aa', VALID_SHA))).toBe(true);
  });

  it.each([
    ['too short', 'a'.repeat(10)],
    ['too long', 'a'.repeat(65)],
    ['uppercase hex', 'A'.repeat(64)],
    ['non-hex characters', 'z'.repeat(64)],
    ['empty string', ''],
    ['path traversal', '../../../../etc/passwd'],
    ['absolute path', '/etc/passwd'.padEnd(64, '0')],
    ['embedded null byte', `${'a'.repeat(63)}\0`],
  ])('rejects a malformed hash: %s', (_label, bad) => {
    expect(() => casPath(bad)).toThrow(TypeError);
  });
});

describe('readCasFile ceiling (N4 -- bounded, not unbounded, reads)', () => {
  let casRoot: string;

  beforeEach(async () => {
    casRoot = await mkdtemp(path.join(tmpdir(), 'publisher-cas-test-'));
    vi.stubEnv('PUBLISHER_CAS_ROOT', casRoot);
    vi.stubEnv('PUBLISHER_CAS_READ_MAX_BYTES', '10');
    vi.resetModules();
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    vi.resetModules();
    await rm(casRoot, { recursive: true, force: true });
  });

  async function writeBlob(sha: string, bytes: number): Promise<void> {
    const { casPath: freshCasPath } = await import('./db.js');
    const blob = freshCasPath(sha);
    await mkdir(path.dirname(blob), { recursive: true });
    await writeFile(blob, 'x'.repeat(bytes));
  }

  it('refuses a blob over the configured ceiling', async () => {
    await writeBlob(VALID_SHA, 11);
    const { readCasFile, CasReadTooLargeError } = await import('./db.js');
    await expect(readCasFile(VALID_SHA)).rejects.toThrow(CasReadTooLargeError);
  });

  it('reads a blob at or under the ceiling normally', async () => {
    await writeBlob(VALID_SHA, 10);
    const { readCasFile } = await import('./db.js');
    await expect(readCasFile(VALID_SHA)).resolves.toBe('x'.repeat(10));
  });
});
