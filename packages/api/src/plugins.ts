/**
 * Cross-cutting plugins (U5/U6): auth + tenancy (S1), idempotency (S3),
 * security headers (S5), tenant-keyed rate limiting (S7), strict CORS.
 *
 * Registered as one Fastify plugin so the onRequest hooks run in a fixed order
 * (auth before idempotency before rate-limit) for every route.
 */
import { createHash, timingSafeEqual } from 'node:crypto';
import cors from '@fastify/cors';
import helmet from '@fastify/helmet';
import rateLimit from '@fastify/rate-limit';
import type { FastifyInstance } from 'fastify';
import { pool } from './db.js';

/** S1 -- constant-time token comparison. Plain `===` on a bearer token leaks
 * timing (and the comma-separated env var is a known seam to be replaced by
 * OIDC introspection in P8 -- this keeps the seam, hardens the comparison). */
function tokensEqual(a: string, b: string): boolean {
  const ba = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;
  return timingSafeEqual(ba, bb);
}

/** Maps a bearer token to a tenant. Replace with OIDC introspection in P8. */
export function resolveTenant(token: string): string | null {
  const configured = process.env.PUBLISHER_API_TOKENS ?? '';
  for (const pair of configured.split(',')) {
    const [t, tenant] = pair.split(':');
    if (t && tenant && tokensEqual(t.trim(), token)) return tenant.trim();
  }
  return null;
}

const PUBLIC_ROUTES = new Set(['/v1/health']);

export async function registerAuthAndSecurity(server: FastifyInstance): Promise<void> {
  // ── Auth / tenancy ─────────────────────────────────────────
  server.addHook('onRequest', async (request, reply) => {
    const url = request.routeOptions?.url ?? request.url;
    if (PUBLIC_ROUTES.has(url)) return;
    // /v1/admin/* is its own auth zone -- each admin route enforces the admin
    // token (PUBLISHER_ADMIN_TOKENS) itself; tenant tokens are not valid here.
    if (url.startsWith('/v1/admin')) return;

    const header = request.headers.authorization ?? '';
    const token = header.startsWith('Bearer ') ? header.slice(7).trim() : '';
    if (!token) {
      return reply.code(401).send({ error: 'missing bearer token' });
    }
    const tenant = resolveTenant(token);
    if (!tenant) {
      return reply.code(401).send({ error: 'invalid token' });
    }
    request.tenantId = tenant;
  });

  // ── Idempotency-Key (U5/S3: reserve BEFORE the handler) ────
  //
  // The old flow checked the cache on onRequest and inserted on onSend --
  // after the handler had already run. Two concurrent retries of the same key
  // both missed the cache, both created a resource, and the second INSERT
  // ON CONFLICT DO NOTHING silently discarded the second record (N2).
  //
  // Now the key is RESERVED before the handler runs: the first request wins
  // the reservation (rowCount 1) and proceeds; a concurrent duplicate finds
  // the reservation (rowCount 0) and gets 409 while the original is in
  // flight, or the stored response once it has completed. onSend fills the
  // reserved row in -- for ANY status, so a failed original replays its
  // failure instead of leaving a perpetual 202 reservation.
  server.addHook('onRequest', async (request, reply) => {
    if (!['POST', 'PATCH', 'PUT'].includes(request.method)) return;
    if (PUBLIC_ROUTES.has(request.routeOptions?.url ?? request.url)) return;
    // /v1/admin/* is its own auth zone (admin token, no tenantId set) -- the
    // idempotency INSERT keys on request.tenantId, so a mutation there would
    // hit a NOT NULL violation. Skip the hook; admin routes today are all
    // GET (metrics), but a future POST must not silently break on tenancy.
    if ((request.routeOptions?.url ?? request.url).startsWith('/v1/admin')) return;

    const key = request.headers['idempotency-key'];
    if (typeof key !== 'string' || !key) {
      return reply.code(400).send({ error: 'Idempotency-Key header is required for mutations' });
    }
    const inserted = await pool.query(
      `INSERT INTO idempotency_keys (tenant_id, idem_key, status_code, body)
       VALUES ($1, $2, 202, '{}'::jsonb)
       ON CONFLICT (tenant_id, idem_key) DO NOTHING`,
      [request.tenantId, key]
    );
    if (inserted.rowCount === 0) {
      // Duplicate: either the original is still in flight (reservation 202)
      // or it has completed and the reservation was filled in.
      const cached = (
        await pool.query(
          'SELECT status_code, body FROM idempotency_keys WHERE tenant_id = $1 AND idem_key = $2',
          [request.tenantId, key]
        )
      ).rows[0];
      if (!cached) {
        // Raced with a concurrent rollback -- treat as a fresh reservation.
        return reply.code(409).send({ error: 'duplicate request, retry' });
      }
      if (cached.status_code === 202) {
        return reply.code(409).send({ error: 'duplicate request still in flight' });
      }
      return reply.code(cached.status_code).send(cached.body);
    }
    request.idemKey = key;
  });

  // Fill in the reserved row. Fires for every response (including error
  // responses); non-JSON payloads (SSE, binary) are skipped.
  server.addHook('onSend', async (request, reply, payload) => {
    if (!request.idemKey) return payload;
    try {
      const body = typeof payload === 'string' ? JSON.parse(payload) : payload;
      await pool.query(
        `UPDATE idempotency_keys SET status_code = $1, body = $2
         WHERE tenant_id = $3 AND idem_key = $4`,
        [reply.statusCode, body, request.tenantId, request.idemKey]
      );
    } catch {
      /* non-JSON payload (SSE, binary): not replayable, skip */
    }
    return payload;
  });

  // ── Plugins ────────────────────────────────────────────────

  // BUILD_PLAN.md §3.12 requires **strict CORS**. `origin: true` reflects
  // whatever Origin the caller sent, i.e. allow-any. Allow-list from config,
  // fail closed to same-origin-only when nothing is configured.
  const allowedOrigins = (process.env.PUBLISHER_CORS_ORIGINS ?? '')
    .split(',')
    .map((o) => o.trim())
    .filter(Boolean);

  await server.register(cors, {
    origin: allowedOrigins.length > 0 ? allowedOrigins : false,
    credentials: true,
  });

  // S5 -- security headers. @fastify/helmet wraps helmet; defaults are fine.
  await server.register(helmet);

  // S7 -- rate-limit keyed on the TENANT, not the IP. One tenant behind one
  // NAT used to exhaust the shared IP bucket while a distributed caller
  // bypassed it. Fall back to IP for unauthenticated (public) routes.
  await server.register(rateLimit, {
    max: 100,
    timeWindow: '1 minute',
    keyGenerator: (request) => request.tenantId ?? request.ip,
  });
}

/** Constant-time compare against the admin token list (U7 metrics). */
export function isAdminToken(token: string): boolean {
  const configured = process.env.PUBLISHER_ADMIN_TOKENS ?? '';
  if (!configured) return false;
  for (const t of configured.split(',')) {
    if (t.trim() && tokensEqual(t.trim(), token)) return true;
  }
  return false;
}
