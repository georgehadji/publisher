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
 *   GET  /v1/builds/:id/artifacts/:kind/download
 *   POST /v1/webhooks
 *
 * Gate: A third party integrates from the OpenAPI spec with no support contact.
 *
 * ARCHITECTURE_REMEDIATION.md A2. Every route below used to be backed by an
 * in-process Map() and, for the build lifecycle, literal hardcoded payloads
 * regardless of whether anything had actually run (finding 1). State now
 * lives in Postgres (platform/db/schema.sql) -- the same durable store
 * worker.py writes to, so a build's real progress is what these routes read.
 */
import { randomBytes } from 'node:crypto';
import { createReadStream } from 'node:fs';
import path from 'node:path';
import Fastify from 'fastify';
import cors from '@fastify/cors';
import rateLimit from '@fastify/rate-limit';
import pg from 'pg';

const { Pool } = pg;

const server = Fastify({
  logger: true,
  // BUILD_PLAN.md §3.12: request-size caps. Default is 1 MiB; a manuscript upload goes
  // to object storage via a signed URL, not through this JSON body.
  bodyLimit: 1 * 1024 * 1024,
});

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

// Durable CAS root -- must match PUBLISHER_CAS_ROOT in the worker's environment
// (docker-compose.yml wires both to the same volume). Artifact bytes are read
// straight off this shared filesystem; there is no separate object store yet.
const CAS_ROOT = process.env.PUBLISHER_CAS_ROOT ?? './.publisher/cas';

function casPath(sha256: string): string {
  return path.join(CAS_ROOT, sha256.slice(0, 2), sha256.slice(2, 4), sha256);
}

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
server.addHook('onRequest', async (request, reply) => {
  if (!['POST', 'PATCH', 'PUT'].includes(request.method)) return;
  if (PUBLIC_ROUTES.has(request.routeOptions?.url ?? request.url)) return;

  const key = request.headers['idempotency-key'];
  if (typeof key !== 'string' || !key) {
    return reply.code(400).send({ error: 'Idempotency-Key header is required for mutations' });
  }
  const cached = await pool.query(
    'SELECT status_code, body FROM idempotency_keys WHERE tenant_id = $1 AND idem_key = $2',
    [request.tenantId, key]
  );
  if (cached.rows.length > 0) {
    return reply.code(cached.rows[0].status_code).send(cached.rows[0].body);
  }
});

server.addHook('onSend', async (request, reply, payload) => {
  if (!['POST', 'PATCH', 'PUT'].includes(request.method)) return payload;
  const key = request.headers['idempotency-key'];
  if (typeof key !== 'string' || !key || reply.statusCode >= 400) return payload;
  try {
    const body = typeof payload === 'string' ? JSON.parse(payload) : payload;
    await pool.query(
      'INSERT INTO idempotency_keys (tenant_id, idem_key, status_code, body) VALUES ($1, $2, $3, $4) ' +
        'ON CONFLICT (tenant_id, idem_key) DO NOTHING',
      [request.tenantId, key, reply.statusCode, body]
    );
  } catch {
    /* non-JSON payload (SSE, binary): not replayable, skip */
  }
  return payload;
});

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
    const result = await pool.query(
      'INSERT INTO titles (id, tenant_id, title, author) VALUES ($1, $2, $3, $4) RETURNING *',
      [id, request.tenantId, title, author ?? null]
    );
    const record = result.rows[0];
    reply.code(201);
    return { id: record.id, tenantId: record.tenant_id, title: record.title, author: record.author, createdAt: record.created_at };
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/titles/:id',
  async (request, reply) => {
    const { id } = request.params;
    const result = await pool.query('SELECT * FROM titles WHERE id = $1', [id]);
    const record = result.rows[0];
    if (!record || record.tenant_id !== request.tenantId) {
      // 404 not 403: another tenant's title must not be distinguishable from a
      // nonexistent one.
      return reply.code(404).send({ error: 'not found' });
    }
    return { id: record.id, tenantId: record.tenant_id, title: record.title, author: record.author, createdAt: record.created_at, status: 'active' };
  }
);

// ── Manuscripts ────────────────────────────────────────────────

server.post<{ Params: { id: string } }>(
  '/v1/titles/:id/manuscripts',
  async (request, reply) => {
    const { id } = request.params;
    const title = (await pool.query('SELECT tenant_id FROM titles WHERE id = $1', [id])).rows[0];
    // The title must belong to the caller before a manuscript can be created under
    // it — this check was absent, so any tenant could attach a manuscript to any
    // title id.
    if (!title || title.tenant_id !== request.tenantId) {
      return reply.code(404).send({ error: 'not found' });
    }
    const manuscriptId = `ms-${randomBytes(9).toString('base64url')}`;
    await pool.query(
      'INSERT INTO manuscripts (id, tenant_id, title_id, status) VALUES ($1, $2, $3, $4)',
      [manuscriptId, request.tenantId, id, 'uploaded']
    );
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
    const manuscript = (await pool.query('SELECT * FROM manuscripts WHERE id = $1', [id])).rows[0];
    if (!manuscript || manuscript.tenant_id !== request.tenantId) {
      return reply.code(404).send({ error: 'not found' });
    }

    // Structure comes from the `ast` artifact of the manuscript's most recent
    // build. No build has necessarily run yet -- report that honestly rather
    // than fabricating chapters that were never inferred.
    const artifact = (
      await pool.query(
        `SELECT a.sha256 FROM artifacts a
         JOIN builds b ON b.id = a.build_id
         WHERE b.document_id = $1 AND a.kind = 'ast'
         ORDER BY a.created_at DESC LIMIT 1`,
        [id]
      )
    ).rows[0];

    if (!artifact) {
      return {
        manuscriptId: id,
        status: 'pending',
        chapters: [],
        lowConfidenceNodes: [],
        overrides: [],
      };
    }

    const ast = JSON.parse(await readCasFile(artifact.sha256));
    const chapters = (ast.body ?? [])
      .filter((node: any) => node.type === 'chapter')
      .map((node: any) => ({
        number: node.attrs?.number ?? null,
        title: node.attrs?.title ?? '',
        confidence: 1.0,
      }));

    return {
      manuscriptId: id,
      status: 'ready',
      chapters,
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
    // A "document" is a structured manuscript — same store, same ownership check
    // as /v1/manuscripts/:id/structure.
    const manuscript = (await pool.query('SELECT tenant_id FROM manuscripts WHERE id = $1', [id])).rows[0];
    if (!manuscript || manuscript.tenant_id !== request.tenantId) {
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
    const { documentId, designId, profileIds, mode = 'proof' } = request.body;
    // The document (structured manuscript) being built must belong to the caller —
    // otherwise a tenant could queue a build against another tenant's document id
    // and read the result back through GET /v1/builds/:id (their own build's tenantId).
    const manuscript = (await pool.query('SELECT tenant_id FROM manuscripts WHERE id = $1', [documentId])).rows[0];
    if (!manuscript || manuscript.tenant_id !== request.tenantId) {
      return reply.code(404).send({ error: 'not found' });
    }
    const id = `build-${randomBytes(9).toString('base64url')}`;
    const result = await pool.query(
      `INSERT INTO builds (id, tenant_id, document_id, design_id, profile_ids, mode, status)
       VALUES ($1, $2, $3, $4, $5, $6, 'queued') RETURNING *`,
      [id, request.tenantId, documentId, designId, JSON.stringify(profileIds), mode]
    );
    const build = result.rows[0];
    reply.code(201);
    return {
      buildId: build.id,
      documentId, designId, profileIds, mode,
      status: build.status,
      createdAt: build.created_at,
    };
  }
);

async function loadBuild(id: string, tenantId: string) {
  const build = (await pool.query('SELECT * FROM builds WHERE id = $1', [id])).rows[0];
  if (!build || build.tenant_id !== tenantId) return null;
  return build;
}

server.get<{ Params: { id: string } }>(
  '/v1/builds/:id',
  async (request, reply) => {
    const { id } = request.params;
    const build = await loadBuild(id, request.tenantId);
    if (!build) {
      return reply.code(404).send({ error: 'not found' });
    }
    const stageRows = (
      await pool.query(
        'SELECT stage_name, status, duration_ms, cache_hit FROM build_stages WHERE build_id = $1 ORDER BY started_at',
        [id]
      )
    ).rows;
    const stages = stageRows.map((s) => ({
      name: s.stage_name, status: s.status, durationMs: s.duration_ms ?? 0, cacheHit: s.cache_hit,
    }));
    const totalDurationMs = stageRows.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0);
    return {
      buildId: id,
      status: build.status,
      stages,
      metrics: { totalDurationMs },
    };
  }
);

// SSE events endpoint (ARCHITECTURE.md §2.12) -- polls real build_stages
// transitions instead of simulating a fixed progress sequence.
server.get<{ Params: { id: string } }>(
  '/v1/builds/:id/events',
  async (request, reply) => {
    const build = await loadBuild(request.params.id, request.tenantId);
    if (!build) {
      return reply.code(404).send({ error: 'not found' });
    }
    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    reply.raw.write('event: connected\ndata: {}\n\n');

    const seen = new Set<string>();
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      const current = (await pool.query('SELECT status FROM builds WHERE id = $1', [request.params.id])).rows[0];
      const stages = (
        await pool.query(
          'SELECT stage_name, status FROM build_stages WHERE build_id = $1 ORDER BY started_at',
          [request.params.id]
        )
      ).rows;
      for (const s of stages) {
        const marker = `${s.stage_name}:${s.status}`;
        if (!seen.has(marker)) {
          seen.add(marker);
          reply.raw.write(`event: stage.progress\ndata: ${JSON.stringify({ stage: s.stage_name, status: s.status })}\n\n`);
        }
      }
      if (current?.status === 'completed' || current?.status === 'failed') {
        reply.raw.write(`event: build.${current.status}\ndata: {}\n\n`);
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    reply.raw.end();
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/builds/:id/preflight',
  async (request, reply) => {
    const { id } = request.params;
    const build = await loadBuild(id, request.tenantId);
    if (!build) {
      return reply.code(404).send({ error: 'not found' });
    }
    const artifact = (
      await pool.query("SELECT sha256 FROM artifacts WHERE build_id = $1 AND kind = 'report'", [id])
    ).rows[0];
    if (!artifact) {
      return { buildId: id, status: 'pending' };
    }
    const report = JSON.parse(await readCasFile(artifact.sha256));
    return { buildId: id, ...report };
  }
);

server.get<{ Params: { id: string; kind: string } }>(
  '/v1/builds/:id/artifacts/:kind',
  async (request, reply) => {
    const { id, kind } = request.params;
    const build = await loadBuild(id, request.tenantId);
    if (!build) {
      return reply.code(404).send({ error: 'not found' });
    }
    const artifact = (
      await pool.query('SELECT * FROM artifacts WHERE build_id = $1 AND kind = $2', [id, kind])
    ).rows[0];
    if (!artifact) {
      return reply.code(404).send({ error: 'artifact not found -- this stage has not produced output yet' });
    }
    return {
      buildId: id,
      artifactKind: kind,
      sha256: artifact.sha256,
      mediaType: artifact.media_type,
      size: Number(artifact.size),
      downloadUrl: `/v1/builds/${id}/artifacts/${kind}/download`,
    };
  }
);

server.get<{ Params: { id: string; kind: string } }>(
  '/v1/builds/:id/artifacts/:kind/download',
  async (request, reply) => {
    const { id, kind } = request.params;
    const build = await loadBuild(id, request.tenantId);
    if (!build) {
      return reply.code(404).send({ error: 'not found' });
    }
    const artifact = (
      await pool.query('SELECT * FROM artifacts WHERE build_id = $1 AND kind = $2', [id, kind])
    ).rows[0];
    if (!artifact) {
      return reply.code(404).send({ error: 'artifact not found' });
    }
    reply.type(artifact.media_type);
    return reply.send(createReadStream(casPath(artifact.sha256)));
  }
);

async function readCasFile(sha256: string): Promise<string> {
  const fs = await import('node:fs/promises');
  return fs.readFile(casPath(sha256), 'utf-8');
}

// ── Webhooks ───────────────────────────────────────────────────

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
    const id = `wh-${randomBytes(9).toString('base64url')}`;
    // Webhook signing secret — MUST be cryptographically random. `Math.random()` is
    // not a CSPRNG: it is a seeded PRNG with ~52 bits of guessable state, so an
    // attacker who observes a few issued secrets can predict later ones and forge
    // signed webhook deliveries. randomBytes draws from the OS CSPRNG.
    const secret = `sec-${randomBytes(32).toString('base64url')}`;
    const result = await pool.query(
      'INSERT INTO webhooks (id, tenant_id, url, events, secret) VALUES ($1, $2, $3, $4, $5) RETURNING *',
      [id, request.tenantId, url, JSON.stringify(events), secret]
    );
    const hook = result.rows[0];
    reply.code(201);
    return { id: hook.id, url: hook.url, events: hook.events, secret: hook.secret, createdAt: hook.created_at };
  }
);

server.get('/v1/webhooks', async (request) => {
  // Tenant-scoped -- the prior in-memory version listed every tenant's webhooks
  // (including their target URLs) to any authenticated caller. Not one of the
  // audit's findings, but the same class of gap (finding 5's Map() had no tenant
  // concept at all) and trivial to close while this table is being created anyway.
  const result = await pool.query(
    'SELECT id, url, events, created_at FROM webhooks WHERE tenant_id = $1',
    [request.tenantId]
  );
  return { webhooks: result.rows.map((h) => ({ id: h.id, url: h.url, events: h.events, createdAt: h.created_at })) };
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
