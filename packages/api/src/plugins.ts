/**
 * Cross-cutting plugins (U5/U6): auth + tenancy (S1), idempotency (S3),
 * security headers (S5), tenant-keyed rate limiting (S7), strict CORS.
 *
 * Registered as one Fastify plugin so the onRequest hooks run in a fixed order
 * (auth before idempotency before rate-limit) for every route.
 *
 * E5.1 -- auth is no longer decided by matching the request URL against a
 * hardcoded set of "special" prefixes (a new route with no entry there used
 * to fall through as an ordinary tenant route, or worse, as unauthenticated
 * if someone added it to the wrong set). Every route now DECLARES its zone
 * via `config: { auth: ... }` (types.ts); this hook reads that declaration
 * and defaults an UNDECLARED route to 'tenant' -- the strictest zone that
 * still lets a plain route work -- so a forgotten declaration fails closed
 * instead of open. `app.ts`'s startup assertion additionally refuses to
 * boot at all if any route has no declaration whatsoever.
 */
import argon2 from 'argon2';
import cors from '@fastify/cors';
import helmet from '@fastify/helmet';
import rateLimit from '@fastify/rate-limit';
import type { FastifyInstance } from 'fastify';
import { withTenant } from './db.js';
import type { AuthRequirement } from './types.js';

/** E5.3 -- PUBLISHER_API_TOKENS/PUBLISHER_ADMIN_TOKENS store an argon2id
 * hash of each token, never the plaintext (T6: a leaked env dump or compose
 * file used to hand over a directly usable bearer token). `argon2.verify`
 * does its own constant-time comparison internally, so there is no separate
 * timing-safe-compare step the way the old plaintext version needed.
 * `packages/api/src/hash-token.ts` generates the hash for a given token --
 * that CLI is how an operator turns a chosen secret into what goes in the
 * env var. A malformed configured hash (e.g. the env var was set to a raw
 * plaintext token by mistake) must not crash auth for every OTHER
 * configured token, hence the per-entry catch.
 *
 * Multiple entries are `;`-separated, NOT `,`-separated: an argon2id PHC
 * string embeds its own parameters as `m=65536,t=3,p=4` -- a literal comma
 * inside the hash -- so `,` cannot be the list delimiter without splitting
 * every hash apart. */
async function verifyHash(hash: string, presented: string): Promise<boolean> {
  try {
    return await argon2.verify(hash, presented);
  } catch {
    return false;
  }
}

/** Maps a bearer token to a tenant. Replace with OIDC introspection in P8. */
export async function resolveTenant(token: string): Promise<string | null> {
  const configured = process.env.PUBLISHER_API_TOKENS ?? '';
  for (const pair of configured.split(';')) {
    const [hash, tenant] = pair.split(':');
    if (hash && tenant && (await verifyHash(hash.trim(), token))) return tenant.trim();
  }
  return null;
}

export async function registerAuthAndSecurity(server: FastifyInstance): Promise<void> {
  // ── Auth / tenancy ─────────────────────────────────────────
  server.addHook('onRequest', async (request, reply) => {
    const auth: AuthRequirement = request.routeOptions?.config?.auth ?? 'tenant';

    if (auth === 'public') {
      // The hook still always assigns a Principal -- it never returns
      // early without one, even for the zone that needs no credentials.
      request.principal = { kind: 'public' };
      return;
    }

    const header = request.headers.authorization ?? '';
    const token = header.startsWith('Bearer ') ? header.slice(7).trim() : '';

    if (auth === 'admin') {
      // PUBLISHER_ADMIN_TOKENS unset means the whole admin zone does not
      // exist in this deployment -- report absent, not merely denied.
      if (!process.env.PUBLISHER_ADMIN_TOKENS) {
        return reply.code(404).send({ error: 'not found' });
      }
      if (token && (await isAdminToken(token))) {
        request.principal = { kind: 'admin' };
        return;
      }
      // A token that resolves as a real TENANT identity is authenticated,
      // just for the wrong zone -- 403 (forbidden), not 401 (no/garbage
      // credentials). Distinguishing the two is the point of L12/T3: an
      // admin route reached with a tenant token must not look like "no
      // token was even tried".
      if (token && (await resolveTenant(token))) {
        return reply.code(403).send({ error: 'admin token required' });
      }
      return reply.code(401).send({ error: token ? 'invalid admin token' : 'missing bearer token' });
    }

    // auth === 'tenant'
    if (!token) {
      return reply.code(401).send({ error: 'missing bearer token' });
    }
    const tenant = await resolveTenant(token);
    if (!tenant) {
      return reply.code(401).send({ error: 'invalid token' });
    }
    request.tenantId = tenant;
    request.principal = { kind: 'tenant', tenantId: tenant };
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
    // Only tenant-zone routes have a request.tenantId to key the reservation
    // on -- a public or admin mutation (none exist today) would otherwise
    // hit a NOT NULL violation on idempotency_keys.tenant_id.
    const auth: AuthRequirement = request.routeOptions?.config?.auth ?? 'tenant';
    if (auth !== 'tenant') return;

    const key = request.headers['idempotency-key'];
    if (typeof key !== 'string' || !key) {
      return reply.code(400).send({ error: 'Idempotency-Key header is required for mutations' });
    }
    const inserted = await withTenant(request.tenantId, (client) =>
      client.query(
        `INSERT INTO idempotency_keys (tenant_id, idem_key, status_code, body)
         VALUES ($1, $2, 202, '{}'::jsonb)
         ON CONFLICT (tenant_id, idem_key) DO NOTHING`,
        [request.tenantId, key]
      )
    );
    if (inserted.rowCount === 0) {
      // Duplicate: either the original is still in flight (reservation 202)
      // or it has completed and the reservation was filled in.
      const cached = (
        await withTenant(request.tenantId, (client) =>
          client.query(
            'SELECT status_code, body FROM idempotency_keys WHERE tenant_id = $1 AND idem_key = $2',
            [request.tenantId, key]
          )
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
      await withTenant(request.tenantId, (client) =>
        client.query(
          `UPDATE idempotency_keys SET status_code = $1, body = $2
           WHERE tenant_id = $3 AND idem_key = $4`,
          [reply.statusCode, body, request.tenantId, request.idemKey]
        )
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

/** Checks a token against the argon2id-hashed admin token list (U7 metrics,
 * E5.3). */
export async function isAdminToken(token: string): Promise<boolean> {
  const configured = process.env.PUBLISHER_ADMIN_TOKENS ?? '';
  if (!configured) return false;
  for (const hash of configured.split(';')) {
    if (hash.trim() && (await verifyHash(hash.trim(), token))) return true;
  }
  return false;
}
