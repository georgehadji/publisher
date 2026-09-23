// ── PATCH /v1/documents/:id/overrides: what may enter the log ─────────────
//
// Validation and the applicability check both run BEFORE the handler touches
// Postgres. The full app cannot show that in-process: its idempotency hook
// (plugins.ts) runs first, and either 400s a PATCH with no Idempotency-Key or
// writes to Postgres for one that has it. An earlier draft of these tests used
// the full app and "passed" every 400 case on the idempotency 400 alone. So the
// route is mounted on a bare Fastify with the app's own AJV_OPTIONS, and every
// 400 below asserts FST_ERR_VALIDATION. DATABASE_URL points at port 1: a request
// that passes both checks reaches loadOwned and fails there with a 500, which is
// how "accepted" is told apart from "refused". Persistence itself -- rows, their
// order, RLS, 409 on a reused id -- is DB-backed, in
// tests/integration/test_override_log.py.

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Fastify, { type FastifyInstance } from 'fastify';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
let app: FastifyInstance;
let APPLICABLE_OPS: readonly string[];
let OVERRIDE_OP_SCHEMA: any;

beforeAll(async () => {
  // Env first, THEN import: db.ts builds its pool from DATABASE_URL at module
  // load, so a static import would bind it before this runs (as app.test.ts notes).
  process.env.DATABASE_URL = 'postgresql://publisher:publisher@localhost:1/publisher';
  const routes = await import('./manuscripts.js');
  ({ APPLICABLE_OPS, OVERRIDE_OP_SCHEMA } = routes);
  const { AJV_OPTIONS } = await import('../app.js');
  app = Fastify({ logger: false, ajv: AJV_OPTIONS });
  app.decorateRequest('tenantId', 'tenant-a');
  await routes.registerManuscripts(app);
  await app.ready();
}, 30_000);

afterAll(async () => {
  await app?.close();
});

const op = (fields: Record<string, unknown> = {}) => ({
  id: 'ov-1',
  sourceRef: { docxId: 'p1' },
  op: 'retitle',
  value: 'One',
  actor: 'user:reviewer',
  at: '2026-01-01T00:00:00Z',
  ...fields,
});

const patch = (body: unknown) =>
  app.inject({
    method: 'PATCH',
    url: '/v1/documents/ms-1/overrides',
    headers: { 'content-type': 'application/json' },
    payload: JSON.stringify(body),
  });

describe('PATCH overrides validation', () => {
  it('a valid, applicable op passes validation and reaches the database', async () => {
    const res = await patch({ ops: [op()] });
    expect(res.statusCode).toBe(500);   // loadOwned, against the unreachable DB
  });

  it.each([
    ['no ops', { ops: [] }],
    ['a missing required field', { ops: [op({ actor: undefined })] }],
    ['a field the schema does not declare', { ops: [op({ colour: 'red' })] }],
    ['a malformed timestamp', { ops: [op({ at: 'yesterday' })] }],
    ['an id outside the ov- namespace', { ops: [op({ id: 'x-1' })] }],
    ['a retitle with no value', { ops: [op({ value: undefined })] }],
    ['a field beside ops', { ops: [op()], extra: true }],
  ])('rejects %s with 400', async (_label, body) => {
    const res = await patch(body);
    expect(res.statusCode).toBe(400);
    expect(res.json().code).toBe('FST_ERR_VALIDATION');   // not some other 400
  });

  it('refuses an op the build cannot apply, naming it', async () => {
    const res = await patch({ ops: [op(), op({ id: 'ov-2', op: 'split', value: undefined })] });
    expect(res.statusCode).toBe(422);
    expect(res.json().error).toContain('ov-2 (split)');
  });
});

describe('the override contract cannot drift', () => {
  it('OVERRIDE_OP_SCHEMA is the overrides/1 op schema, verbatim', () => {
    const schema = JSON.parse(
      readFileSync(path.join(REPO_ROOT, 'schemas/overrides/overrides.schema.json'), 'utf-8')
    );
    expect(OVERRIDE_OP_SCHEMA).toEqual(schema.$defs.overrideOp);
  });

  it('APPLICABLE_OPS is the schema enum minus what resolve cannot apply', () => {
    const python = readFileSync(
      path.join(REPO_ROOT, 'services/structure/publisher_structure/overrides.py'), 'utf-8'
    );
    const block = python.match(/UNIMPLEMENTED_OPS = frozenset\(\{([^}]*)\}\)/);
    expect(block, 'UNIMPLEMENTED_OPS not found in overrides.py').not.toBeNull();
    const unimplemented = new Set([...block![1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]));
    const expected = (OVERRIDE_OP_SCHEMA.properties.op.enum as string[])
      .filter((o) => !unimplemented.has(o));
    expect([...APPLICABLE_OPS].sort()).toEqual([...expected].sort());
  });
});
