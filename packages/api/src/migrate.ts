/**
 * E4.1 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- the migration runner. Not a
 * framework, per this repo's own doctrine: a ledger table plus applying
 * whatever numbered *.sql files in platform/db/migrations/ it hasn't seen
 * yet, each in its own transaction. `node-pg-migrate` is the documented
 * upgrade path if this ever needs more than that.
 *
 * Run directly (`node dist/migrate.js`) or via the `migrate` docker-compose
 * service, which `worker`/`api` depend on with
 * `condition: service_completed_successfully` -- this is the ENTIRE fix for
 * L14 ("an existing deployment has no path to a schema change"): the old
 * docker-entrypoint-initdb.d mount only ever ran against an empty data
 * directory.
 */
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const { Pool } = pg;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// dist/migrate.js -> repo root is three levels up (packages/api/dist/..).
const MIGRATIONS_DIR = path.resolve(__dirname, '../../../platform/db/migrations');

async function main(): Promise<void> {
  const pool = new Pool({ connectionString: process.env.DATABASE_URL });
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
          version    INTEGER PRIMARY KEY,
          name       TEXT NOT NULL,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    `);
    const applied = new Set(
      (await pool.query('SELECT version FROM schema_migrations')).rows.map((r) => r.version)
    );
    const files = readdirSync(MIGRATIONS_DIR)
      .filter((f) => f.endsWith('.sql'))
      .sort();
    for (const file of files) {
      const version = parseInt(file.split('_')[0], 10);
      if (applied.has(version)) {
        continue;
      }
      const sql = readFileSync(path.join(MIGRATIONS_DIR, file), 'utf-8');
      console.log(`applying ${file}`);
      const client = await pool.connect();
      try {
        await client.query('BEGIN');
        await client.query(sql);
        await client.query('INSERT INTO schema_migrations (version, name) VALUES ($1, $2)', [
          version,
          file,
        ]);
        await client.query('COMMIT');
      } catch (err) {
        await client.query('ROLLBACK');
        throw new Error(`migration ${file} failed: ${(err as Error).message}`, { cause: err });
      } finally {
        client.release();
      }
    }
    console.log('migrations up to date');
  } finally {
    await pool.end();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
