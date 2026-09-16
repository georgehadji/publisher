/**
 * E0.4 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- unit tests for the auth
 * primitives. No DB, no server: these are pure functions over env vars.
 *
 * E5.3 -- PUBLISHER_API_TOKENS/PUBLISHER_ADMIN_TOKENS now store an argon2id
 * hash of each token, not the plaintext, so these tests hash real tokens
 * with the same `argon2` package plugins.ts verifies against.
 */
import argon2 from 'argon2';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { isAdminToken, resolveTenant } from './plugins.js';

const ORIGINAL_ENV = { ...process.env };

let hashA: string;
let hashB: string;
let hashLong: string;
let hashAdmin: string;

beforeAll(async () => {
  [hashA, hashB, hashLong, hashAdmin] = await Promise.all(
    ['tok-a', 'tok-b', 'tok-a-long', 'admin-secret'].map((t) => argon2.hash(t))
  );
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

describe('resolveTenant', () => {
  it('returns null when PUBLISHER_API_TOKENS is unset', async () => {
    delete process.env.PUBLISHER_API_TOKENS;
    expect(await resolveTenant('anything')).toBeNull();
  });

  it('maps a configured token to its tenant', async () => {
    // ';', not ',' -- an argon2id hash embeds its own params as
    // "m=65536,t=3,p=4", a literal comma, so ',' can't be the list delimiter.
    process.env.PUBLISHER_API_TOKENS = `${hashA}:tenant-a;${hashB}:tenant-b`;
    expect(await resolveTenant('tok-a')).toBe('tenant-a');
    expect(await resolveTenant('tok-b')).toBe('tenant-b');
  });

  it('tolerates whitespace around hashes and tenants', async () => {
    process.env.PUBLISHER_API_TOKENS = ` ${hashA} : tenant-a `;
    expect(await resolveTenant('tok-a')).toBe('tenant-a');
  });

  it('returns null for a token not in the configured list', async () => {
    process.env.PUBLISHER_API_TOKENS = `${hashA}:tenant-a`;
    expect(await resolveTenant('tok-does-not-exist')).toBeNull();
  });

  it('does not substring-match a token that is only a prefix of a real one', async () => {
    process.env.PUBLISHER_API_TOKENS = `${hashLong}:tenant-a`;
    expect(await resolveTenant('tok-a')).toBeNull();
  });

  it('rejects a plaintext value where an argon2id hash was expected', async () => {
    // The pre-E5.3 config format ("token:tenant" in plaintext) must not
    // silently keep working -- that would defeat the point of hashing.
    process.env.PUBLISHER_API_TOKENS = 'tok-a:tenant-a';
    expect(await resolveTenant('tok-a')).toBeNull();
  });
});

describe('isAdminToken', () => {
  it('is false when PUBLISHER_ADMIN_TOKENS is unset', async () => {
    delete process.env.PUBLISHER_ADMIN_TOKENS;
    expect(await isAdminToken('anything')).toBe(false);
  });

  it('is true for a configured admin token', async () => {
    process.env.PUBLISHER_ADMIN_TOKENS = hashAdmin;
    expect(await isAdminToken('admin-secret')).toBe(true);
  });

  it('is false for a tenant token presented as an admin token', async () => {
    process.env.PUBLISHER_ADMIN_TOKENS = hashAdmin;
    expect(await isAdminToken('some-tenant-token')).toBe(false);
  });
});
