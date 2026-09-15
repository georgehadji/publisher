/**
 * Entry point -- the application lives in src/app.ts (U6 split); this file
 * owns process lifecycle only (listen, U5/S10 graceful shutdown), so
 * `createApp()` stays import-safe for tests (E0.4).
 */
import { createApp } from './app.js';
import { pool } from './db.js';

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
