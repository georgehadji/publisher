/**
 * /v1/admin/metrics (U7) -- stage-level p50/p95 from data build_stages has
 * ALWAYS been collecting (duration_ms) but nobody ever read. Read-only.
 *
 * E5.1 -- `config: { auth: 'admin' }` is the route's only auth statement;
 * the plugin hook enforces it (admin token required, PUBLISHER_ADMIN_TOKENS
 * unset => route reports 404 "effectively absent", a tenant token gets 403)
 * before this handler ever runs. No schema change needed.
 */
import type { FastifyInstance } from 'fastify';
import { adminPool } from '../db.js';

export async function registerAdmin(server: FastifyInstance): Promise<void> {
  server.get('/v1/admin/metrics', { config: { auth: 'admin' } }, async (_request, _reply) => {
    const stages = (
      await adminPool.query(
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
      await adminPool.query(
        `SELECT error_kind, count(*) AS count
         FROM builds WHERE error_kind IS NOT NULL
         GROUP BY error_kind ORDER BY count DESC`
      )
    ).rows;

    const queue = (
      await adminPool.query(
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
