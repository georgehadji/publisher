/**
 * Builds: queue, status, SSE events (U5/S11), preflight (U6 split).
 *
 * The SSE endpoint no longer polls two queries every 500 ms per client (N3):
 * the worker sends `NOTIFY build_<id>` on every stage and on terminal state,
 * and this route LISTENs on a dedicated connection. The same wire format
 * (`event: stage.progress` / `event: build.completed|failed`) is preserved.
 */
import { randomBytes } from 'node:crypto';
import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import { loadOwned, pool, readCasFile } from '../db.js';

// Build ids are server-generated `build-<base64url>` tokens; the SSE channel
// name is interpolated into SQL below (LISTEN takes a literal), so the shape
// is asserted before use.
const SAFE_ID = /^[A-Za-z0-9_-]+$/;

// U8 admission control: one tenant queueing unbounded builds must not starve
// every other tenant or degrade the builds_queued_idx scan
// (ARCHITECTURE_UPLIFT_PLAN.md U8). This caps in-flight (queued + running)
// builds per tenant, not lifetime builds.
const MAX_QUEUED_BUILDS_PER_TENANT = parseInt(process.env.PUBLISHER_MAX_QUEUED_BUILDS_PER_TENANT ?? '50', 10);

export async function registerBuilds(server: FastifyInstance): Promise<void> {
  server.post<{ Body: { documentId: string; designId: string; profileIds: string[]; mode?: string } }>(
    '/v1/builds',
    async (request, reply) => {
      const { documentId, designId, profileIds, mode = 'proof' } = request.body;
      // The document (structured manuscript) being built must belong to the
      // caller -- otherwise a tenant could queue a build against another
      // tenant's document id and read the result back through its own build.
      const manuscript = await loadOwned('manuscripts', documentId, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }
      const inFlight = await pool.query(
        "SELECT count(*)::int AS count FROM builds WHERE tenant_id = $1 AND status IN ('queued', 'running')",
        [request.tenantId]
      );
      if (inFlight.rows[0].count >= MAX_QUEUED_BUILDS_PER_TENANT) {
        return reply.code(429).send({
          error: 'too many in-flight builds for this tenant',
          limit: MAX_QUEUED_BUILDS_PER_TENANT,
        });
      }
      const id = `build-${randomBytes(9).toString('base64url')}`;
      // U7: a correlation id joins the HTTP request to the worker's logs. The
      // worker puts it on every log record; nothing else needs to know it.
      const correlationId = randomBytes(16).toString('hex');
      const result = await pool.query(
        `INSERT INTO builds (id, tenant_id, document_id, design_id, profile_ids, mode, status, correlation_id)
         VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7) RETURNING *`,
        [id, request.tenantId, documentId, designId, JSON.stringify(profileIds), mode, correlationId]
      );
      const build = result.rows[0];
      reply.code(201);
      return {
        buildId: build.id,
        documentId, designId, profileIds, mode,
        status: build.status,
        correlationId: build.correlation_id,
        createdAt: build.created_at,
      };
    }
  );

  server.get<{ Params: { id: string } }>(
    '/v1/builds/:id',
    async (request, reply) => {
      const build = await loadOwned('builds', request.params.id, request.tenantId);
      if (!build) {
        return reply.code(404).send({ error: 'not found' });
      }
      const stageRows = (
        await pool.query(
          'SELECT stage_name, status, duration_ms, cache_hit FROM build_stages WHERE build_id = $1 ORDER BY started_at',
          [request.params.id]
        )
      ).rows;
      const stages = stageRows.map((s) => ({
        name: s.stage_name, status: s.status, durationMs: s.duration_ms ?? 0, cacheHit: s.cache_hit,
      }));
      const totalDurationMs = stageRows.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0);
      return {
        buildId: request.params.id,
        status: build.status,
        stages,
        metrics: { totalDurationMs },
      };
    }
  );

  // SSE events endpoint (ARCHITECTURE.md §2.12). U5/S11: push-based via
  // Postgres LISTEN/NOTIFY instead of the old two-query-per-500ms poll loop
  // (N3). The worker NOTIFYs build_<id> after every stage and on terminal
  // state; this route holds a dedicated LISTEN connection per client.
  server.get<{ Params: { id: string } }>(
    '/v1/builds/:id/events',
    async (request, reply) => {
      const { id } = request.params;
      const build = await loadOwned('builds', id, request.tenantId);
      if (!build) {
        return reply.code(404).send({ error: 'not found' });
      }
      if (!SAFE_ID.test(id)) {
        return reply.code(404).send({ error: 'not found' });
      }

      const channel = `build_${id}`;
      const seen = new Set<string>();
      let closed = false;
      // True once reply.hijack() hands raw socket control to this handler.
      // Before that point the response is Fastify-managed: close() must NOT
      // call reply.raw.end() or it tears down the socket before Fastify can
      // send its own error response (the client gets a truncated stream
      // instead of a proper 500 when setup fails).
      let hijacked = false;
      let client: pg.PoolClient | null = null;

      const writeEvent = (event: string, data: unknown) => {
        reply.raw.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
      };

      const close = async () => {
        if (closed) return;
        closed = true;
        clearTimeout(timer);
        const held = client;
        client = null;
        if (held) {
          try {
            await held.query('UNLISTEN *');
          } catch {
            /* connection already broken */
          }
          held.release();
        }
        if (hijacked) {
          reply.raw.end();
        }
      };

      // 30 s keep-alive cap (same as the polling version) so abandoned
      // connections cannot pin a LISTEN slot forever.
      const timer = setTimeout(() => void close(), 30_000);
      request.raw.on('close', () => void close());

      try {
        client = await pool.connect();
        // LISTEN BEFORE the response headers are sent: once the client sees
        // 'connected', the subscription must already be active, or a stage
        // completing in that window is lost (the old poller had a blind spot
        // of up to 500 ms; the push design must have none).
        const held = client;
        held.on('notification', (msg) => {
          if (!msg.payload || closed) return;
          const parsed = JSON.parse(msg.payload);
          if (parsed.stage) {
            const marker = `${parsed.stage}:${parsed.status}`;
            if (seen.has(marker)) return;
            seen.add(marker);
            writeEvent('stage.progress', { stage: parsed.stage, status: parsed.status });
          } else if (parsed.status) {
            writeEvent(`build.${parsed.status}`, {});
            void close();
          }
        });
        // LISTEN takes an identifier, and the channel name embeds the build id
        // (`build_build-<base64url>`), whose `-` is a syntax error unquoted --
        // the worker NOTIFYs via parameterized pg_notify (no such problem),
        // but this side must quote. SAFE_ID already excludes `"`, so the
        // doubling is belt-and-braces against a future id-shape change.
        await held.query(`LISTEN "${channel.replace(/"/g, '""')}"`);

        // The handler returns after setting up the subscription; hijack so
        // Fastify does not finalize the response when it does (the standard
        // Fastify SSE pattern -- without it, the raw stream is closed as soon
        // as the handler resolves).
        reply.hijack();
        hijacked = true;
        reply.raw.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        });
        reply.raw.write('event: connected\ndata: {}\n\n');

        // Initial snapshot: anything that happened before we subscribed.
        const current = (
          await pool.query('SELECT status FROM builds WHERE id = $1', [id])
        ).rows[0];
        const stages = (
          await pool.query(
            'SELECT stage_name, status FROM build_stages WHERE build_id = $1 ORDER BY started_at',
            [id]
          )
        ).rows;
        for (const s of stages) {
          const marker = `${s.stage_name}:${s.status}`;
          if (!seen.has(marker)) {
            seen.add(marker);
            writeEvent('stage.progress', { stage: s.stage_name, status: s.status });
          }
        }
        if (current?.status === 'completed' || current?.status === 'failed' || current?.status === 'dead') {
          writeEvent(`build.${current.status}`, {});
          await close();
        }
      } catch (err) {
        request.log.error({ err, buildId: id }, 'SSE setup failed');
        if (!hijacked) {
          // Setup failed before the socket was hijacked: Fastify owns the
          // response here, so send a real error instead of an empty stream.
          return reply.code(500).send({ error: 'SSE setup failed' });
        }
        await close();
      }
    }
  );

  server.get<{ Params: { id: string } }>(
    '/v1/builds/:id/preflight',
    async (request, reply) => {
      const build = await loadOwned('builds', request.params.id, request.tenantId);
      if (!build) {
        return reply.code(404).send({ error: 'not found' });
      }
      const artifact = (
        // preflight/1, not kind='report' -- three stages emit kind='report', and
        // this route was returning finish's report as the preflight verdict.
        await pool.query(
          "SELECT sha256 FROM artifacts WHERE build_id = $1 AND schema_id = 'preflight/1'",
          [request.params.id]
        )
      ).rows[0];
      if (!artifact) {
        return { buildId: request.params.id, status: 'pending' };
      }
      const report = JSON.parse(await readCasFile(artifact.sha256));
      return { buildId: request.params.id, ...report };
    }
  );
}
