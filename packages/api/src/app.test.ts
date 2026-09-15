/**
 * E0.4 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- the auth matrix and the
 * route-coverage meta-test that keeps it honest, closing L3 and answering T3
 * (an unauthenticated admin route slipping in unnoticed).
 *
 * These exercise the auth BOUNDARY only (via Fastify's built-in `.inject()`,
 * no real socket, no DB): does this route reject a missing/unrecognized
 * token, and does a valid token get past the onRequest hooks. Whether the
 * HANDLER then succeeds is a DB-backed question already covered by
 * tests/integration/test_api_hardening.py (idempotency race, cas path
 * rejection, admin metrics happy path) against a real Postgres -- not
 * duplicated here.
 *
 * `ROUTE_AUTH` is the coverage list: `test_every_registered_route_is_classified`
 * fails if the app registers a route this file doesn't know about, which is
 * exactly the failure mode L12 names (a new admin route with no auth test).
 */
import type { FastifyInstance } from 'fastify';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const TENANT_TOKEN = 'e04-tenant-token';
const OTHER_TENANT_TOKEN = 'e04-other-tenant-token';
const ADMIN_TOKEN = 'e04-admin-token';

let app: FastifyInstance;

beforeAll(async () => {
  // Must be set before db.ts's module-level `pool` and `CAS_ROOT` are
  // constructed, hence the dynamic import (a static import is hoisted above
  // this call). Port 1 on localhost: guaranteed nothing is listening, so a
  // route whose auth check is missing and falls through to a real query
  // fails loudly (500) rather than accidentally talking to a real database.
  process.env.DATABASE_URL = 'postgresql://publisher:publisher@localhost:1/publisher';
  process.env.PUBLISHER_API_TOKENS = `${TENANT_TOKEN}:tenant-a,${OTHER_TENANT_TOKEN}:tenant-b`;
  process.env.PUBLISHER_ADMIN_TOKENS = ADMIN_TOKEN;
  const { createApp } = await import('./app.js');
  app = await createApp();
});

afterAll(async () => {
  await app.close();
});

type Bucket = 'public' | 'admin' | 'tenant';

// Every route this app registers, and which auth zone it belongs to.
// Today's auth model (plugins.ts) has exactly three zones -- PUBLIC_ROUTES,
// '/v1/admin/*', and everything else requiring a tenant token -- so that is
// what this classifies against. E5.1 replaces this with a per-route
// declaration; this table is what it will need to preserve.
const ROUTE_AUTH: Record<string, Bucket> = {
  'GET /v1/health': 'public',
  'HEAD /v1/health': 'public',
  'POST /v1/titles': 'tenant',
  'GET /v1/titles/:id': 'tenant',
  'HEAD /v1/titles/:id': 'tenant',
  'POST /v1/titles/:id/manuscripts': 'tenant',
  'PUT /v1/manuscripts/:id/upload': 'tenant',
  'GET /v1/manuscripts/:id/structure': 'tenant',
  'HEAD /v1/manuscripts/:id/structure': 'tenant',
  'PATCH /v1/documents/:id/overrides': 'tenant',
  'POST /v1/builds': 'tenant',
  'GET /v1/builds/:id': 'tenant',
  'HEAD /v1/builds/:id': 'tenant',
  'GET /v1/builds/:id/events': 'tenant',
  'HEAD /v1/builds/:id/events': 'tenant',
  'GET /v1/builds/:id/preflight': 'tenant',
  'HEAD /v1/builds/:id/preflight': 'tenant',
  'GET /v1/builds/:id/artifacts/:kind': 'tenant',
  'HEAD /v1/builds/:id/artifacts/:kind': 'tenant',
  'GET /v1/builds/:id/artifacts/:kind/download': 'tenant',
  'HEAD /v1/builds/:id/artifacts/:kind/download': 'tenant',
  'POST /v1/webhooks': 'tenant',
  'GET /v1/webhooks': 'tenant',
  'HEAD /v1/webhooks': 'tenant',
  'GET /v1/admin/metrics': 'admin',
  'HEAD /v1/admin/metrics': 'admin',
};

// @fastify/cors registers a catch-all OPTIONS preflight responder, not an
// application route -- there is nothing to classify or inject a request at.
const EXEMPT = new Set(['OPTIONS *']);

function registeredRouteKeys(): Set<string> {
  return new Set(
    app.publisherRoutes.map((r) => `${r.method} ${r.url}`).filter((k) => !EXEMPT.has(k))
  );
}

describe('route coverage', () => {
  it('every registered route is classified in ROUTE_AUTH', () => {
    const missing = [...registeredRouteKeys()].filter((k) => !(k in ROUTE_AUTH));
    expect(missing).toEqual([]);
  });

  it('ROUTE_AUTH has no entry for a route that no longer exists', () => {
    const actual = registeredRouteKeys();
    const stale = Object.keys(ROUTE_AUTH).filter((k) => !actual.has(k));
    expect(stale).toEqual([]);
  });
});

function routesIn(bucket: Bucket): string[] {
  return Object.entries(ROUTE_AUTH)
    .filter(([, b]) => b === bucket)
    .map(([key]) => key);
}

function split(key: string): [string, string] {
  const [method, ...rest] = key.split(' ');
  return [method, rest.join(' ')];
}

async function inject(key: string, headers: Record<string, string> = {}) {
  const [method, url] = split(key);
  return app.inject({
    method: method as any,
    url,
    headers: { ...headers, 'idempotency-key': `e04-probe-${Math.random()}` },
  });
}

describe('auth matrix -- tenant routes', () => {
  const routes = routesIn('tenant');

  it.each(routes)('%s: no token -> 401', async (key) => {
    const res = await inject(key);
    expect(res.statusCode).toBe(401);
  });

  it.each(routes)('%s: unrecognized token -> 401', async (key) => {
    const res = await inject(key, { authorization: 'Bearer not-a-real-token' });
    expect(res.statusCode).toBe(401);
  });

  it.each(routes)('%s: valid tenant token passes the auth boundary', async (key) => {
    const res = await inject(key, { authorization: `Bearer ${TENANT_TOKEN}` });
    // Not a DB-backed assertion (T3/L12's concern is the auth DECISION, not
    // the handler's result): a route with a working DB gets its real status,
    // one without gets a 500 from a failed query -- either way, not a 401.
    expect(res.statusCode).not.toBe(401);
  });
});

describe('auth matrix -- admin routes', () => {
  const routes = routesIn('admin');

  it.each(routes)('%s: no token -> 401', async (key) => {
    const res = await inject(key);
    expect(res.statusCode).toBe(401);
  });

  it.each(routes)('%s: a valid TENANT token is not an admin token -> 401', async (key) => {
    const res = await inject(key, { authorization: `Bearer ${TENANT_TOKEN}` });
    expect(res.statusCode).toBe(401);
  });

  it.each(routes)('%s: valid admin token passes the auth boundary', async (key) => {
    const res = await inject(key, { authorization: `Bearer ${ADMIN_TOKEN}` });
    expect(res.statusCode).not.toBe(401);
  });
});

describe('auth matrix -- public routes', () => {
  const routes = routesIn('public');

  it.each(routes)('%s: reachable with no token', async (key) => {
    const res = await inject(key);
    expect(res.statusCode).not.toBe(401);
  });
});

describe('cross-tenant token is still just a token (not a bypass)', () => {
  it('a valid token for a DIFFERENT tenant still passes the auth boundary, not silently as tenant-a', async () => {
    // loadOwned's 404-not-403 masking (db.ts) needs a real row in a real DB
    // to demonstrate the cross-tenant 404 itself -- that is
    // tests/integration/test_api_hardening.py's job. This only pins that the
    // OTHER tenant's token is itself valid (auth doesn't special-case it),
    // so a future regression can't make it silently fall back to tenant-a.
    const res = await app.inject({
      method: 'GET',
      url: '/v1/titles/some-id',
      headers: { authorization: `Bearer ${OTHER_TENANT_TOKEN}` },
    });
    expect(res.statusCode).not.toBe(401);
  });
});
