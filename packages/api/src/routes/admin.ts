/**
 * /v1/admin/metrics (U7) -- stage-level p50/p95 from data build_stages has
 * ALWAYS been collecting (duration_ms) but nobody ever read. Read-only,
 * admin-token gated (PUBLISHER_ADMIN_TOKENS; unconfigured => the route is
 * effectively absent). No schema change needed.
 */
import type { FastifyInstance } from 'fastify';
import { pool } from '../db.js';
import { isAdminToken } from '../plugins.js';

export async function registerAdmin(server: FastifyInstance): Promise<void> {
  server.get('/v1/admin/metrics', async (request, reply) => {
    if (!process.env.PUBLISHER_ADMIN_TOKENS) {
      return reply.code(404).send({ error: 'not found' });
    }
    const header = request.headers.authorization ?? '';
    const token = header.startsWith('Bearer ') ? header.slice(7).trim() : '';
    if (!token || !isAdminToken(token)) {
      return reply.code(401).send({ error: 'invalid admin token' });
    }

    const stages = (
      await pool.query(
        `SELECT stage_name,
                count(*) AS runs,
                count(*) FILTER (WHERE status = 'failed') AS failures,
                round(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1) AS p50_ms,
                round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1) AS p95_ms
         FROM build_stages
         GROUP BY stage_name
         ORDER BY stage_name`
      )
    ).rows;

    const failures = (
      await pool.query(
        `SELECT error_kind, count(*) AS count
         FROM builds WHERE error_kind IS NOT NULL
         GROUP BY error_kind ORDER BY count DESC`
      )
    ).rows;

    const queue = (
      await pool.query(
        `SELECT status, count(*) AS count FROM builds GROUP BY status ORDER BY status`
      )
    ).rows;

    return {
      generatedAt: new Date().toISOString(),
      stages,
      failuresByKind: failures,
      buildsByStatus: queue,
    };
  });
}
