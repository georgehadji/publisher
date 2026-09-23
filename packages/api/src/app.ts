/**
 * Publisher API application (U6 split -- formerly the single 700-line
 * index.ts). Assembles the Fastify instance and registers the auth/security
 * plugins and the per-resource route modules. Process lifecycle (listen,
 * U5/S10 graceful shutdown) lives in index.ts, the documented boot path --
 * `createApp()` builds the app with no side effects, so E0.4's vitest suite
 * can import and exercise it (via `.inject()`) without binding a real port.
 */
import Fastify from 'fastify';
import './types.js';
import type { AuthRequirement } from './types.js';
import { registerAuthAndSecurity } from './plugins.js';
import { registerHealth } from './routes/health.js';
import { registerTitles } from './routes/titles.js';
import { registerManuscripts } from './routes/manuscripts.js';
import { registerBuilds } from './routes/builds.js';
import { registerArtifacts } from './routes/artifacts.js';
import { registerWebhooks } from './routes/webhooks.js';
import { registerAdmin } from './routes/admin.js';

// E5.1 -- registered by @fastify/cors as a catch-all preflight responder,
// not an application route; it carries no `config` of its own to declare an
// auth zone for, and isn't one.
const CORS_PREFLIGHT_ROUTE = 'OPTIONS *';

/** Fail-closed at deploy time, not at request time: a route with NO `auth`
 * declaration at all (not even the implicit 'tenant' default the runtime
 * hook applies) means someone registered a route and this file's own
 * bookkeeping never saw a `config` object -- almost certainly a
 * `server.<method>(url, handler)` two-arg call with no options, which is
 * exactly the "next route quietly skips the auth check" shape L12 names. */
export function assertEveryRouteDeclaresAuth(
  routes: { method: string; url: string; auth: AuthRequirement | undefined }[]
): void {
  const undeclared = routes.filter(
    (r) => r.auth === undefined && `${r.method} ${r.url}` !== CORS_PREFLIGHT_ROUTE
  );
  if (undeclared.length > 0) {
    throw new Error(
      'fail-closed startup check (E5.1): route(s) registered with no auth zone declared -- ' +
        undeclared.map((r) => `${r.method} ${r.url}`).join(', ')
    );
  }
}

// D1 fix: Fastify's default AJV config coerces types (coerceTypes: 'array'),
// so `{"ops": "not-an-array"}` silently became `["not-an-array"]` and passed
// the `type: 'array'` schema check. Validation must be strict: a string is
// NOT an array, and a route that accepts it is accepting a type its schema
// says it rejects. No route depends on coercion (verified: no numeric
// request params anywhere), so disabling it changes nothing else.
//
// removeAdditional: false for the same reason. Fastify's default (true) DELETES
// a property a schema's `additionalProperties: false` forbids, then passes the
// request -- so an override op carrying an undeclared field was silently
// trimmed and stored as something other than what the reviewer sent.
// `additionalProperties: false` means reject; this makes it mean that. Only
// the overrides route declares it, so nothing else changes.
//
// Exported so a route's validation can be tested under the app's exact rules.
export const AJV_OPTIONS = {
  customOptions: { coerceTypes: false, removeAdditional: false },
};

export async function createApp() {
  const server = Fastify({
    // Vitest sets process.env.VITEST -- quiets pino's per-request JSON lines
    // during the auth-matrix suite (E0.4), which injects dozens of requests.
    logger: !process.env.VITEST,
    // BUILD_PLAN.md §3.12: request-size caps. Default is 1 MiB; a manuscript
    // upload goes to the CAS via its own streamed route, not through the JSON
    // body parser.
    bodyLimit: 1 * 1024 * 1024,
    ajv: AJV_OPTIONS,
  });

  // E0.4 -- route-coverage meta-test needs to enumerate every registered
  // route; `onRoute` is Fastify's documented hook for exactly that, and must
  // be added before any route registration below to see all of them.
  server.decorate('publisherRoutes', [] as { method: string; url: string; auth: AuthRequirement | undefined }[]);
  server.addHook('onRoute', (opts) => {
    for (const method of ([] as string[]).concat(opts.method)) {
      server.publisherRoutes.push({ method, url: opts.url, auth: opts.config?.auth });
    }
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

  assertEveryRouteDeclaresAuth(server.publisherRoutes);

  return server;
}
