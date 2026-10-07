// ── PATCH /v1/documents/:id/overrides: what may enter the log ─────────────
//
// Validation runs BEFORE the handler touches
// Postgres. The full app cannot show that in-process: its idempotency hook
// (plugins.ts) runs first, and either 400s a PATCH with no Idempotency-Key or
// writes to Postgres for one that has it. An earlier draft of these tests used
// the full app and "passed" every 400 case on the idempotency 400 alone. So the
// route is mounted on a bare Fastify with the app's own AJV_OPTIONS, and every
// 400 below asserts FST_ERR_VALIDATION. DATABASE_URL points at port 1: a request
// that passes validation reaches loadOwned and fails there with a 500, which is
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
let OVERRIDE_OP_SCHEMA: any;

beforeAll(async () => {
  // Env first, THEN import: db.ts builds its pool from DATABASE_URL at module
  // load, so a static import would bind it before this runs (as app.test.ts notes).
  process.env.DATABASE_URL = 'postgresql://publisher:publisher@localhost:1/publisher';
  const routes = await import('./manuscripts.js');
  ({ OVERRIDE_OP_SCHEMA } = routes);
  const { AJV_OPTIONS } = await import('../app.js');
  app = Fastify({ logger: false, ajv: AJV_OPTIONS });
  app.decorateRequest('tenantId', 'tenant-a');
  // The auth hook is not mounted here; a test header stands in for the
  // principal it would have resolved (plugins.ts, W2).
  app.decorateRequest('principal', null as any);
  app.addHook('onRequest', async (request) => {
    const reviewer = request.headers['x-test-reviewer'];
    request.principal = typeof reviewer === 'string'
      ? { kind: 'reviewer', tenantId: 'tenant-a', reviewer }
      : { kind: 'tenant', tenantId: 'tenant-a' };
  });
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
    ['a set_attr with no path', { ops: [op({ op: 'set_attr', value: 'recto' })] }],
    ['an insert with no value', { ops: [op({ op: 'insert', value: undefined })] }],
    ['rename, which the schema dropped', { ops: [op({ op: 'rename' })] }],
    ['a field beside ops', { ops: [op()], extra: true }],
    ['a heading level that is not 1-6 (B3)',
      { ops: [op({ op: 'reclassify', from: 'paragraph', to: 'heading', value: 7 })] }],
  ])('rejects %s with 400', async (_label, body) => {
    const res = await patch(body);
    expect(res.statusCode).toBe(400);
    expect(res.json().code).toBe('FST_ERR_VALIDATION');   // not some other 400
  });

  it('accepts every op the schema declares: resolve applies them all', async () => {
    const ops = (OVERRIDE_OP_SCHEMA.properties.op.enum as string[]).map((name, i) =>
      // `to` is not `heading`: the shared `value` 'One' would not be a level (B3).
      op({ id: `ov-${i}`, op: name, from: 'paragraph', to: 'blockquote', path: '/attrs/role',
           rationale: 'why' }));
    const res = await patch({ ops });
    expect(res.statusCode).toBe(500);   // past validation, into loadOwned
  });
});

describe('a reviewer writes as themself (W2)', () => {
  const asReviewer = (body: unknown) =>
    app.inject({
      method: 'PATCH',
      url: '/v1/documents/ms-1/overrides',
      headers: { 'content-type': 'application/json', 'x-test-reviewer': 'ada' },
      payload: JSON.stringify(body),
    });

  it("refuses an op attributed to anyone but the token's reviewer", async () => {
    const res = await asReviewer({ ops: [op({ actor: 'user:ada' }), op({ id: 'ov-2', actor: 'user:bob' })] });
    expect(res.statusCode).toBe(403);
    expect(res.json().error).toContain('ov-2');
  });

  it("accepts the reviewer's own ops", async () => {
    const res = await asReviewer({ ops: [op({ actor: 'user:ada' })] });
    expect(res.statusCode).toBe(500);   // past the actor check, into loadOwned
  });

  it('a tenant (service) token names its own actor, as agents do', async () => {
    const res = await patch({ ops: [op({ actor: 'agent:wrangler@v1' })] });
    expect(res.statusCode).toBe(500);
  });
});

describe('POST proposals/:pid/accept (W4)', () => {
  const accept = (pid: string, body: unknown, reviewer?: string) =>
    app.inject({
      method: 'POST',
      url: `/v1/manuscripts/ms-1/proposals/${pid}/accept`,
      headers: { 'content-type': 'application/json', ...(reviewer ? { 'x-test-reviewer': reviewer } : {}) },
      payload: JSON.stringify(body),
    });

  it('an actor and a well-formed proposal id reach the database', async () => {
    expect((await accept('pr-abc', { actor: 'user:local-reviewer' })).statusCode).toBe(500);
  });

  it.each([
    ['no actor', 'pr-abc', {}],
    ['a body that tries to say what the op does', 'pr-abc', { actor: 'user:a', op: 'delete' }],
    ['an id outside the pr- namespace', 'ov-abc', { actor: 'user:a' }],
  ])('rejects %s with 400', async (_label, pid, body) => {
    const res = await accept(pid, body);
    expect(res.statusCode).toBe(400);
    expect(res.json().code).toBe('FST_ERR_VALIDATION');
  });

  it('a reviewer accepts only as themself', async () => {
    expect((await accept('pr-abc', { actor: 'user:bob' }, 'ada')).statusCode).toBe(403);
    expect((await accept('pr-abc', { actor: 'user:ada' }, 'ada')).statusCode).toBe(500);
  });
});

describe('the override contract cannot drift', () => {
  it('OVERRIDE_OP_SCHEMA is the overrides/1 op schema, verbatim', () => {
    const schema = JSON.parse(
      readFileSync(path.join(REPO_ROOT, 'schemas/overrides/overrides.schema.json'), 'utf-8')
    );
    expect(OVERRIDE_OP_SCHEMA).toEqual(schema.$defs.overrideOp);
  });

  it('resolve implements every op, so the route needs no refusal list', () => {
    // If an op is ever added to the schema without a transform, it must go in
    // UNIMPLEMENTED_OPS -- and this route must refuse it again (422), or one
    // stored op would fail every later build of its manuscript.
    const python = readFileSync(
      path.join(REPO_ROOT, 'services/structure/publisher_structure/override_ops.py'), 'utf-8'
    );
    expect(python).toMatch(/^UNIMPLEMENTED_OPS: frozenset = frozenset\(\)$/m);
  });
});
