/**
 * Publisher API application (U6 split -- formerly the single 700-line
 * index.ts). Assembles the Fastify instance, registers the auth/security
 * plugins and the per-resource route modules, and owns the process lifecycle
 * (U5/S10 graceful shutdown).
 */
import Fastify from 'fastify';
import './types.js';
import { pool } from './db.js';
import { registerAuthAndSecurity } from './plugins.js';
import { registerHealth } from './routes/health.js';
import { registerTitles } from './routes/titles.js';
import { registerManuscripts } from './routes/manuscripts.js';
import { registerBuilds } from './routes/builds.js';
import { registerArtifacts } from './routes/artifacts.js';
import { registerWebhooks } from './routes/webhooks.js';
import { registerAdmin } from './routes/admin.js';

export async function createApp() {
  const server = Fastify({
    logger: true,
    // BUILD_PLAN.md §3.12: request-size caps. Default is 1 MiB; a manuscript
    // upload goes to the CAS via its own streamed route, not through the JSON
    // body parser.
    bodyLimit: 1 * 1024 * 1024,
    // D1 fix: Fastify's default AJV config coerces types (coerceTypes: 'array'),
    // so `{"ops": "not-an-array"}` silently became `["not-an-array"]` and passed
    // the `type: 'array'` schema check. Validation must be strict: a string is
    // NOT an array, and a route that accepts it is accepting a type its schema
    // says it rejects. No route depends on coercion (verified: no numeric
    // request params anywhere), so disabling it changes nothing else.
    ajv: {
      customOptions: { coerceTypes: false },
    },
  });

  // Auth + idempotency hooks and the security plugins MUST be registered
  // before the routes so their onRequest hooks cover every route.
  await registerAuthAndSecurity(server);
  await registerHealth(server);
  await registerTitles(server);
  await registerManuscripts(server);
  await registerBuilds(server);
  await registerArtifacts(server);
  await registerWebhooks(server);
  await registerAdmin(server);

  return server;
}

const PORT = parseInt(process.env.PORT || '4000', 10);
const HOST = process.env.HOST || '0.0.0.0';

const server = await createApp();

// U5/S10 -- graceful shutdown: stop accepting, drain in-flight requests
// (including 30-second SSE streams), then close the pool. Every deploy used to
// drop in-flight work outright.
let shuttingDown = false;
async function shutdown(signal: string) {
  if (shuttingDown) return;
  shuttingDown = true;
  server.log.info({ signal }, 'shutting down');
  // N6 fix: server.close() waits for in-flight requests to finish, and a stuck
  // connection (e.g. an SSE client that never closes) could otherwise hold the
  // process open forever. Give the drain a hard ceiling, then force-exit --
  // a container that cannot die is worse than one that drops stragglers.
  const force = setTimeout(() => {
    server.log.warn('graceful shutdown timed out -- forcing exit');
    process.exit(0);
  }, 10_000);
  force.unref();
  try {
    await server.close();
    clearTimeout(force);
    await pool.end();
    process.exit(0);
  } catch (err) {
    server.log.error({ err }, 'shutdown failed');
    process.exit(1);
  }
}
process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('SIGINT', () => void shutdown('SIGINT'));

try {
  await server.listen({ port: PORT, host: HOST });
  console.log(`Publisher API running at http://${HOST}:${PORT}`);
} catch (err) {
  server.log.error(err);
  process.exit(1);
}
