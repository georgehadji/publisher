/**
 * Publisher API Server — Fastify.
 *
 * Routes from ARCHITECTURE.md §2.12 and BUILD_PLAN.md §3.20:
 *   POST /v1/titles
 *   POST /v1/titles/:id/manuscripts
 *   GET  /v1/manuscripts/:id/structure
 *   PATCH /v1/documents/:id/overrides
 *   POST /v1/builds
 *   GET  /v1/builds/:id
 *   GET  /v1/builds/:id/events (SSE)
 *   GET  /v1/builds/:id/preflight
 *   GET  /v1/builds/:id/artifacts/:kind
 *   POST /v1/webhooks
 *
 * Gate: A third party integrates from the OpenAPI spec with no support contact.
 */
import { randomBytes } from 'node:crypto';
import Fastify from 'fastify';
import cors from '@fastify/cors';
import rateLimit from '@fastify/rate-limit';

const server = Fastify({
  logger: true,
  // BUILD_PLAN.md §3.12: request-size caps. Default is 1 MiB; a manuscript upload goes
  // to object storage via a signed URL, not through this JSON body.
  bodyLimit: 1 * 1024 * 1024,
});

// ── Auth, tenancy, idempotency (BUILD_PLAN.md §3.12) ───────────
//
// §3.12 specifies "OIDC + short-lived tenant-scoped tokens · Idempotency-Key on every
// mutation · request-size caps · strict CORS". Every route previously ran unauthenticated
// and untenanted: any caller could read or mutate any title, and a retried POST created
// duplicates. Token verification here is a seam — swap `resolveTenant` for real OIDC
// introspection without touching the routes.

declare module 'fastify' {
  interface FastifyRequest {
    tenantId: string;
  }
}

/** Maps a bearer token to a tenant. Replace with OIDC introspection in P8. */
function resolveTenant(token: string): string | null {
  const configured = process.env.PUBLISHER_API_TOKENS ?? '';
  for (const pair of configured.split(',')) {
    const [t, tenant] = pair.split(':');
    if (t && tenant && t.trim() === token) return tenant.trim();
  }
  return null;
}

const PUBLIC_ROUTES = new Set(['/v1/health']);

server.addHook('onRequest', async (request, reply) => {
  if (PUBLIC_ROUTES.has(request.routeOptions?.url ?? request.url)) return;

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

/**
 * Idempotency-Key replay for mutations. A retried POST/PATCH with the same key returns
 * the first response instead of creating a second resource. Keyed by tenant so two
 * tenants cannot collide on (or read) each other's keys.
 */
const idempotencyStore = new Map<string, { status: number; body: unknown }>();

server.addHook('onRequest', async (request, reply) => {
  if (!['POST', 'PATCH', 'PUT'].includes(request.method)) return;
  if (PUBLIC_ROUTES.has(request.routeOptions?.url ?? request.url)) return;

  const key = request.headers['idempotency-key'];
  if (typeof key !== 'string' || !key) {
    return reply.code(400).send({ error: 'Idempotency-Key header is required for mutations' });
  }
  const cached = idempotencyStore.get(`${request.tenantId}:${key}`);
  if (cached) {
    return reply.code(cached.status).send(cached.body);
  }
});

server.addHook('onSend', async (request, reply, payload) => {
  if (!['POST', 'PATCH', 'PUT'].includes(request.method)) return payload;
  const key = request.headers['idempotency-key'];
  if (typeof key !== 'string' || !key || reply.statusCode >= 400) return payload;
  const storeKey = `${request.tenantId}:${key}`;
  if (!idempotencyStore.has(storeKey)) {
    try {
      idempotencyStore.set(storeKey, {
        status: reply.statusCode,
        body: typeof payload === 'string' ? JSON.parse(payload) : payload,
      });
    } catch {
      /* non-JSON payload (SSE, binary): not replayable, skip */
    }
  }
  return payload;
});

/** 404 rather than 403 for another tenant's resource — do not leak existence. */
function assertTenant(resource: { tenantId?: string } | undefined, tenantId: string): boolean {
  return !!resource && resource.tenantId === tenantId;
}

// ── Plugins ────────────────────────────────────────────────────

// BUILD_PLAN.md §3.12 requires **strict CORS**. `origin: true` reflects whatever Origin
// the caller sent, i.e. allow-any — which, combined with credentialed requests, lets any
// site drive this API as the logged-in user. Allow-list from config instead, and fail
// closed to same-origin-only when nothing is configured.
const allowedOrigins = (process.env.PUBLISHER_CORS_ORIGINS ?? '')
  .split(',')
  .map((o) => o.trim())
  .filter(Boolean);

await server.register(cors, {
  origin: allowedOrigins.length > 0 ? allowedOrigins : false,
  credentials: true,
});
await server.register(rateLimit, {
  max: 100,
  timeWindow: '1 minute',
});

// ── Health ─────────────────────────────────────────────────────

server.get('/v1/health', async () => {
  return {
    status: 'ok',
    version: '0.1.0',
    timestamp: new Date().toISOString(),
    build: 'publisher-api',
  };
});

// ── Tenant-scoped stores ───────────────────────────────────────
// In-memory for now (P8 moves these to Postgres). The point here is that every record
// carries its tenantId and every read checks it — cross-tenant access is a 404, not a
// silent success, which is what the routes did when they fabricated responses.

interface Title { id: string; tenantId: string; title: string; author?: string; createdAt: string }
interface Build { id: string; tenantId: string; documentId: string; status: string; createdAt: string }
// A "document" is a structured manuscript — there is no separate creation endpoint, so
// /v1/documents/:id/overrides addresses the same record as /v1/manuscripts/:id/structure
// by the same id. One store, both routes' tenant check against it.
interface Manuscript { id: string; tenantId: string; titleId: string; status: string; createdAt: string }

const titles = new Map<string, Title>();
const builds = new Map<string, Build>();
const manuscripts = new Map<string, Manuscript>();

// ── Titles ─────────────────────────────────────────────────────

server.post<{ Body: { title: string; author?: string } }>(
  '/v1/titles',
  {
    schema: {
      body: {
        type: 'object',
        required: ['title'],
        properties: {
          title: { type: 'string', minLength: 1 },
          author: { type: 'string' },
        },
      },
    },
  },
  async (request, reply) => {
    const { title, author } = request.body;
    // randomBytes, not Math.random: a guessable resource id is an enumeration vector
    // once these are tenant-scoped.
    const id = `title-${randomBytes(9).toString('base64url')}`;
    const record: Title = {
      id, tenantId: request.tenantId, title, author,
      createdAt: new Date().toISOString(),
    };
    titles.set(id, record);
    reply.code(201);
    return record;
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/titles/:id',
  async (request, reply) => {
    const { id } = request.params;
    const record = titles.get(id);
    if (!assertTenant(record, request.tenantId)) {
      // 404 not 403: another tenant's title must not be distinguishable from a
      // nonexistent one.
      return reply.code(404).send({ error: 'not found' });
    }
    return { ...record, status: 'active' };
  }
);

// ── Manuscripts ────────────────────────────────────────────────

server.post<{ Params: { id: string } }>(
  '/v1/titles/:id/manuscripts',
  async (request, reply) => {
    const { id } = request.params;
    // The title must belong to the caller before a manuscript can be created under
    // it — this check was absent, so any tenant could attach a manuscript to any
    // title id.
    if (!assertTenant(titles.get(id), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    const manuscriptId = `ms-${randomBytes(9).toString('base64url')}`;
    manuscripts.set(manuscriptId, {
      id: manuscriptId,
      tenantId: request.tenantId,
      titleId: id,
      status: 'uploaded',
      createdAt: new Date().toISOString(),
    });
    reply.code(201);
    return {
      manuscriptId,
      titleId: id,
      status: 'uploaded',
      uploadUrl: `/v1/manuscripts/${manuscriptId}/upload`,
    };
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/manuscripts/:id/structure',
  async (request, reply) => {
    const { id } = request.params;
    if (!assertTenant(manuscripts.get(id), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    return {
      manuscriptId: id,
      status: 'ready',
      chapters: [
        { number: 1, title: 'Chapter 1', confidence: 0.95 },
        { number: 2, title: 'Chapter 2', confidence: 0.85 },
      ],
      lowConfidenceNodes: [],
      overrides: [],
    };
  }
);

// ── Overrides ──────────────────────────────────────────────────

server.patch<{ Params: { id: string }; Body: { ops: unknown[] } }>(
  '/v1/documents/:id/overrides',
  async (request, reply) => {
    const { id } = request.params;
    // A "document" is a structured manuscript (see the Manuscript interface note) —
    // same store, same ownership check as /v1/manuscripts/:id/structure.
    if (!assertTenant(manuscripts.get(id), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    const { ops } = request.body;
    return {
      documentId: id,
      applied: ops.length,
      timestamp: new Date().toISOString(),
    };
  }
);

// ── Builds ─────────────────────────────────────────────────────

server.post<{ Body: { documentId: string; designId: string; profileIds: string[]; mode?: string } }>(
  '/v1/builds',
  async (request, reply) => {
    // The document (structured manuscript) being built must belong to the caller —
    // otherwise a tenant could queue a build against another tenant's document id
    // and read the result back through GET /v1/builds/:id (their own build's tenantId).
    if (!assertTenant(manuscripts.get(request.body.documentId), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    const buildRecord: Build = {
      id: `build-${randomBytes(9).toString('base64url')}`,
      tenantId: request.tenantId,
      documentId: request.body.documentId,
      status: 'queued',
      createdAt: new Date().toISOString(),
    };
    builds.set(buildRecord.id, buildRecord);
    const { documentId, designId, profileIds, mode = 'proof' } = request.body;
    reply.code(201);
    return {
      buildId: buildRecord.id,
      documentId, designId, profileIds, mode,
      status: buildRecord.status,
      createdAt: buildRecord.createdAt,
    };
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/builds/:id',
  async (request, reply) => {
    const { id } = request.params;
    if (!assertTenant(builds.get(id), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    return {
      buildId: id,
      status: 'completed',
      stages: [
        { name: 'acquire', status: 'completed', durationMs: 120 },
        { name: 'extract', status: 'completed', durationMs: 340 },
        { name: 'structure', status: 'completed', durationMs: 890 },
        { name: 'design-compile', status: 'completed', durationMs: 45 },
        { name: 'paginate', status: 'completed', durationMs: 4300 },
        { name: 'finish', status: 'completed', durationMs: 2100 },
        { name: 'package', status: 'completed', durationMs: 30 },
      ],
      metrics: { totalDurationMs: 7825, pageCount: 124 },
    };
  }
);

// SSE events endpoint (ARCHITECTURE.md §2.12)
server.get<{ Params: { id: string } }>(
  '/v1/builds/:id/events',
  async (request, reply) => {
    // Same tenant check as GET /v1/builds/:id — this route was missing it, so any
    // authenticated tenant could stream another tenant's build progress by ID.
    if (!assertTenant(builds.get(request.params.id), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    reply.raw.write('event: connected\ndata: {}\n\n');

    // Simulate build progress
    const stages = ['acquire', 'extract', 'structure', 'design', 'paginate', 'finish', 'package'];
    for (const stage of stages) {
      await new Promise((r) => setTimeout(r, 100));
      reply.raw.write(
        `event: stage.progress\ndata: ${JSON.stringify({ stage, status: 'running' })}\n\n`
      );
    }
    reply.raw.write('event: build.completed\ndata: {}\n\n');
    reply.raw.end();
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/builds/:id/preflight',
  async (request, reply) => {
    const { id } = request.params;
    if (!assertTenant(builds.get(id), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    return {
      buildId: id,
      status: 'pass',
      summary: { passed: 8, failed: 0, warnings: 1 },
      checks: [
        { code: 'trim-size', status: 'pass', severity: 'info', humanMessage: 'Trim size OK' },
        { code: 'bleed', status: 'pass', severity: 'info', humanMessage: 'Bleed OK' },
        { code: 'embed-fonts', status: 'warn', severity: 'warning', humanMessage: 'Font embedding advisory' },
      ],
    };
  }
);

server.get<{ Params: { id: string; kind: string } }>(
  '/v1/builds/:id/artifacts/:kind',
  async (request, reply) => {
    const { id, kind } = request.params;
    if (!assertTenant(builds.get(id), request.tenantId)) {
      return reply.code(404).send({ error: 'not found' });
    }
    return {
      buildId: id,
      artifactKind: kind,
      downloadUrl: `https://artifacts.publisher.internal/builds/${id}/${kind}`,
      mediaType: 'application/pdf',
      size: 2048576,
    };
  }
);

// ── Webhooks ───────────────────────────────────────────────────

interface Webhook {
  id: string;
  url: string;
  events: string[];
  secret: string;
  createdAt: string;
}

const webhooks: Webhook[] = [];

server.post<{ Body: { url: string; events: string[] } }>(
  '/v1/webhooks',
  {
    schema: {
      body: {
        type: 'object',
        required: ['url', 'events'],
        properties: {
          url: { type: 'string', format: 'uri' },
          events: {
            type: 'array',
            items: { type: 'string', enum: ['build.completed', 'build.failed', 'gate.awaiting'] },
          },
        },
      },
    },
  },
  async (request, reply) => {
    const { url, events } = request.body;
    const hook: Webhook = {
      id: `wh-${Date.now()}`,
      url,
      events,
      // Webhook signing secret — MUST be cryptographically random. `Math.random()` is
      // not a CSPRNG: it is a seeded PRNG with ~52 bits of guessable state, so an
      // attacker who observes a few issued secrets can predict later ones and forge
      // signed webhook deliveries. randomBytes draws from the OS CSPRNG.
      secret: `sec-${randomBytes(32).toString('base64url')}`,
      createdAt: new Date().toISOString(),
    };
    webhooks.push(hook);
    reply.code(201);
    return hook;
  }
);

server.get('/v1/webhooks', async () => {
  return { webhooks: webhooks.map((h) => ({ id: h.id, url: h.url, events: h.events, createdAt: h.createdAt })) };
});

// ── Start ──────────────────────────────────────────────────────

const PORT = parseInt(process.env.PORT || '4000', 10);
const HOST = process.env.HOST || '0.0.0.0';

try {
  await server.listen({ port: PORT, host: HOST });
  console.log(`Publisher API running at http://${HOST}:${PORT}`);
} catch (err) {
  server.log.error(err);
  process.exit(1);
}

export { server };
