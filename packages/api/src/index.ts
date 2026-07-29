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
import Fastify from 'fastify';
import cors from '@fastify/cors';
import rateLimit from '@fastify/rate-limit';

const server = Fastify({
  logger: true,
});

// ── Plugins ────────────────────────────────────────────────────

await server.register(cors, { origin: true });
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
    const id = `title-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    reply.code(201);
    return { id, title, author, createdAt: new Date().toISOString() };
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/titles/:id',
  async (request, reply) => {
    const { id } = request.params;
    return {
      id,
      title: `Title ${id}`,
      status: 'active',
    };
  }
);

// ── Manuscripts ────────────────────────────────────────────────

server.post<{ Params: { id: string } }>(
  '/v1/titles/:id/manuscripts',
  async (request, reply) => {
    const { id } = request.params;
    const manuscriptId = `ms-${Date.now()}`;
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
  async (request) => {
    const { id } = request.params;
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
  async (request) => {
    const { id } = request.params;
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
    const buildId = `bld-${Date.now()}`;
    reply.code(201);
    return {
      buildId,
      documentId,
      designId,
      profileIds,
      mode,
      status: 'queued',
      createdAt: new Date().toISOString(),
    };
  }
);

server.get<{ Params: { id: string } }>(
  '/v1/builds/:id',
  async (request) => {
    const { id } = request.params;
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
  async (request) => {
    const { id } = request.params;
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
  async (request) => {
    const { id, kind } = request.params;
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
      secret: `sec-${Math.random().toString(36).slice(2)}`,
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
