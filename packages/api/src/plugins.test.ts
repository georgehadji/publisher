/**
 * E0.4 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- unit tests for the auth
 * primitives. No DB, no server: these are pure functions over env vars.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { isAdminToken, resolveTenant } from './plugins.js';

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

describe('resolveTenant', () => {
  it('returns null when PUBLISHER_API_TOKENS is unset', () => {
    delete process.env.PUBLISHER_API_TOKENS;
    expect(resolveTenant('anything')).toBeNull();
  });

  it('maps a configured token to its tenant', () => {
    process.env.PUBLISHER_API_TOKENS = 'tok-a:tenant-a,tok-b:tenant-b';
    expect(resolveTenant('tok-a')).toBe('tenant-a');
    expect(resolveTenant('tok-b')).toBe('tenant-b');
  });

  it('tolerates whitespace around tokens and tenants', () => {
    process.env.PUBLISHER_API_TOKENS = ' tok-a : tenant-a ';
    expect(resolveTenant('tok-a')).toBe('tenant-a');
  });

  it('returns null for a token not in the configured list', () => {
    process.env.PUBLISHER_API_TOKENS = 'tok-a:tenant-a';
    expect(resolveTenant('tok-does-not-exist')).toBeNull();
  });

  it('does not substring-match a token that is only a prefix of a real one', () => {
    process.env.PUBLISHER_API_TOKENS = 'tok-a-long:tenant-a';
    expect(resolveTenant('tok-a')).toBeNull();
  });
});

describe('isAdminToken', () => {
  it('is false when PUBLISHER_ADMIN_TOKENS is unset', () => {
    delete process.env.PUBLISHER_ADMIN_TOKENS;
    expect(isAdminToken('anything')).toBe(false);
  });

  it('is true for a configured admin token', () => {
    process.env.PUBLISHER_ADMIN_TOKENS = 'admin-secret';
    expect(isAdminToken('admin-secret')).toBe(true);
  });

  it('is false for a tenant token presented as an admin token', () => {
    process.env.PUBLISHER_ADMIN_TOKENS = 'admin-secret';
    expect(isAdminToken('some-tenant-token')).toBe(false);
  });
});
