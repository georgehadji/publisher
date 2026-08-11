/**
 * /v1/health (U7) -- must prove the instance can actually serve, not just
 * answer. A load balancer routing on `status: 'ok'` used to happily route to
 * an instance whose database was gone. Now it checks Postgres (short timeout)
 * and CAS readability, and reports 503 with a per-check breakdown when either
 * is down.
 */
import type { FastifyInstance } from 'fastify';
import { access } from 'node:fs/promises';
import { CAS_ROOT, pool } from '../db.js';

export async function registerHealth(server: FastifyInstance): Promise<void> {
  server.get('/v1/health', async (_request, reply) => {
    const checks: Record<string, string> = {};

    try {
      await Promise.race([
        pool.query('SELECT 1'),
        new Promise((_, reject) => setTimeout(() => reject(new Error('postgres health check timed out')), 1500)),
      ]);
      checks.postgres = 'ok';
    } catch {
      checks.postgres = 'down';
    }

    try {
      await access(CAS_ROOT);
      checks.cas = 'ok';
    } catch {
      checks.cas = 'down';
    }

    const ok = Object.values(checks).every((c) => c === 'ok');
    reply.code(ok ? 200 : 503);
    return {
      status: ok ? 'ok' : 'degraded',
      version: '0.1.0',
      timestamp: new Date().toISOString(),
      build: 'publisher-api',
      checks,
    };
  });
}
