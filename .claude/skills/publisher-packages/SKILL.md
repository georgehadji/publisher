---
name: publisher-packages
description: "Map of the `packages/` folder — the TypeScript surfaces: `packages/api` (Fastify + Postgres REST API — titles, manuscripts, uploads, builds, SSE, artifacts, webhooks, health, admin metrics) and `packages/web` (Next.js structure-review UI). Use this whenever a task touches an HTTP route, auth/tenancy, idempotency, rate limiting, artifact download, build status or SSE events, webhook validation, or the review UI. Read before editing anything under packages/."
---

# `packages/` — the TypeScript surfaces

Two workspaces. **`packages/api` owns no pipeline logic** — it queues builds and serves
artifacts; the worker does the work.

## `packages/api` — Fastify + Postgres REST API

Node 22+, ESM, Fastify 5, `pg`, Zod. Scripts: `build` (tsc), `dev` (`tsx watch`),
`start`, `test` (vitest), `lint` (`tsc --noEmit`).

| File | What it does |
|---|---|
| `src/index.ts` | Entry shim only — six lines, imports `./app.js`. Kept so `tsx src/index.ts` / `node dist/index.js` boot paths did not change in the U6 split. |
| `src/app.ts` | Assembles the Fastify instance: registers plugins and every route module, owns process lifecycle and graceful shutdown (U5/S10). Formerly a single 700-line `index.ts`. |
| `src/db.ts` | **The single data-access layer.** Everything touching Postgres or the CAS filesystem lives here so routes stay thin. **E4.2/E4.3:** the pool connects as `publisher_app` (unprivileged — see `platform/db/migrations/003_least_privilege_roles.sql`); `withTenant(tenantId, fn)` checks out a client, sets the `app.tenant_id` GUC the Row-Level Security policies key on via `SELECT set_config('app.tenant_id', $1, true)` for the transaction, then runs `fn`. **E5.2: the raw pool is NOT exported** — `withTenant`/`loadOwned` are the only way a route reaches a tenant table, so an unscoped `pool.query` in a route fails `tsc`, not just `tools/lint_tenant_scoping.py` at runtime-adjacent lint time. The three non-tenant-table call sites that legitimately need a connection get their own named export instead: `openListenConnection` (builds.ts's SSE LISTEN), `pingDatabase` (health.ts), `closePools` (index.ts's graceful shutdown). `adminPool` stays exported — it's the deliberate BYPASSRLS escape valve for `routes/admin.ts`'s cross-tenant aggregates, not general pool access. `loadOwned` (internally `withTenant`-wrapped) centralizes the tenant-ownership check against a table whitelist (never interpolated input). Also `casPath` (validates `^[a-f0-9]{64}$` before `path.join` — S2/N5), `readCasFile` (size ceiling — S6/N4), `casBlobExists`, `DELIVERABLE_SCHEMAS`, `CAS_ROOT`. |
| `src/migrate.ts` | **E4.1** — the migration runner: applies pending `platform/db/migrations/*.sql` in order, tracked in a `schema_migrations` ledger. Compiles to `dist/migrate.js`, run via `npm run migrate` or the `migrate` docker-compose service. Resolves the migrations directory as three levels up from its own compiled location — `Dockerfile.api`'s final stage keeps the same `packages/api/dist` layout as local dev specifically so this resolution doesn't need a special case for containers. |
| `src/hash-token.ts` | **E5.3** — ops CLI (`npm run hash-token -- <token>`) that prints the argon2id hash of a token: the only supported way to turn a chosen bearer secret into what actually goes in `PUBLISHER_API_TOKENS`/`PUBLISHER_ADMIN_TOKENS`. |
| `src/plugins.ts` | Cross-cutting Fastify plugins registered as one unit so the `onRequest` hooks run in a fixed order: **auth + tenancy (S1) → idempotency (S3) → tenant-keyed rate limit (S7)**, plus helmet security headers (S5) and strict CORS. **E5.1:** the auth hook reads each route's declared `config.auth` (`'tenant' \| 'admin' \| 'public'`, types.ts) instead of matching the URL against a hardcoded prefix set, defaults an undeclared route to `'tenant'`, and always assigns `request.principal` — a tenant token reaching an admin route gets 403 (authenticated, wrong zone), no token at all gets 401. `resolveTenant`/`isAdminToken` are `async` (E5.3: they `argon2.verify` against a configured hash, not compare plaintext). Its idempotency hooks go through `withTenant` too (`idempotency_keys` is RLS-scoped since E4.2), and skip on any non-`'tenant'` route the same declaration-based way. |
| `src/types.ts` | Fastify request augmentation — `request.tenantId`, `request.principal` (E5.1's `Principal` union, set by the auth hook for EVERY request), `request.idemKey`. Also declares `FastifyContextConfig.auth` (`AuthRequirement`), the declaration-merging seam every route's `config: { auth: ... }` type-checks against. |
| `src/app.ts` (again) | **E5.1** — `assertEveryRouteDeclaresAuth`, called at the end of `createApp()`: walks every route `onRoute` recorded and throws if any has no `config.auth` at all (not even the implicit default), so a route that skips the declaration fails to boot, not just to authenticate correctly at request time. |
| `src/routes/titles.ts` | `POST /v1/titles`, `GET /v1/titles/:id`, `POST /v1/titles/:id/manuscripts`. **There is no list endpoint** — no `GET /v1/titles`. All `config: { auth: 'tenant' }`. |
| `src/routes/manuscripts.ts` | `PUT /v1/manuscripts/:id/upload` — streams the body straight into the shared CAS (never through the 1 MiB JSON body parser), records `manuscripts.source_sha256`, checks the DOCX/ZIP `PK` magic bytes. Also `GET /v1/manuscripts/:id/structure` and **`PATCH`** `/v1/documents/:id/overrides` (it takes an `ops` array body — the write side of the override layer, not a read). All `config: { auth: 'tenant' }`. |
| `src/routes/builds.ts` | `POST /v1/builds`, `GET /v1/builds/:id`, `GET /v1/builds/:id/preflight`, `GET /v1/builds/:id/events` (SSE). **No `GET /v1/builds` list endpoint** — every GET is `:id`-scoped. All `config: { auth: 'tenant' }`. The SSE endpoint **LISTENs on a dedicated connection** (`openListenConnection`, db.ts) for the worker's `NOTIFY build_<id>` instead of polling two queries every 500 ms per client (N3); wire format `event: stage.progress` / `build.completed\|failed` is unchanged. |
| `src/routes/artifacts.ts` | `GET /v1/builds/:id/artifacts/:kind` (metadata) and `.../download` (byte streaming off the CAS). Both `config: { auth: 'tenant' }`. |
| `src/routes/webhooks.ts` | `POST/GET /v1/webhooks` with S4 URL validation at creation: https only; rejects hosts resolving to RFC1918, loopback, link-local, multicast, or cloud-metadata addresses. **DNS-rebinding re-checks must also run at delivery time** — the seam is marked in the file. Both `config: { auth: 'tenant' }`. |
| `src/routes/health.ts` | `GET /v1/health` — `config: { auth: 'public' }`. Actually checks Postgres (short timeout, via `pingDatabase`) and CAS readability, returning 503 with a per-check breakdown. It used to return unconditional 200 `ok`, so a load balancer happily routed to an instance whose database was gone. |
| `src/routes/admin.ts` | `GET /v1/admin/metrics` — `config: { auth: 'admin' }`; the plugin hook enforces the admin token (and the "PUBLISHER_ADMIN_TOKENS unset ⇒ route effectively absent, 404" case) before the handler runs, so the route body itself is now pure query logic. Stage-level p50/p95 from `build_stages.duration_ms`, data that was always collected and never read. Queries `adminPool` (not the tenant pool) — these are GLOBAL cross-tenant aggregates by design, not scoped by `withTenant`. |
| `package.json`, `tsconfig.json` | `tsconfig.json` **extends the monorepo root** (`packages/web` deliberately does not — see **publisher-root**) — `Dockerfile.api` must preserve the `repo-root/packages/api/` layout or `../../tsconfig.json` resolves to nothing. `argon2` (E5.3, native binding via prebuilt binaries — no native toolchain needed) is a dependency. |

### Environment
`DATABASE_URL` (the `publisher_app` role since E4.3), `PUBLISHER_ADMIN_DATABASE_URL` (the
`publisher_admin` BYPASSRLS role, `adminPool`), `PORT`, `HOST`, `PUBLISHER_API_TOKENS`
(**E5.3:** `<argon2id-hash>:tenant` pairs, `;`-separated — NOT `,`, since an argon2id hash
embeds its own params as a literal comma (`m=65536,t=3,p=4`); generate a hash with
`npm run hash-token -- <token>`), `PUBLISHER_CORS_ORIGINS`,
`PUBLISHER_CAS_ROOT`, `PUBLISHER_ADMIN_TOKENS` (same argon2id-hash format), plus the two
size ceilings in `db.ts`:
`PUBLISHER_UPLOAD_MAX_BYTES` (default 100 MiB) and `PUBLISHER_CAS_READ_MAX_BYTES` (default 64 MiB)
— a rejected large upload is a config change, not a source patch. The API is both a CAS **reader** (artifact
downloads) and, since the upload route landed, a CAS **writer** — the compose mount must not be `:ro`.

## `packages/web` — Next.js review UI

Next 15 + React 19, `output: 'standalone'`, typed routes.

| File | What it does |
|---|---|
| `src/types.ts` | The API contract for the UI: `StructureReview`, `ChapterReview`, `LowConfidenceNode`, `OverrideOp`, `ReviewSession`. Covers structure review, quality review (widow/orphan/runt/river with one-click fixes), and the raster view with proposal-outcome instrumentation. |
| `src/review/StructureReviewPanel.tsx` | The first human gate — chapter map, style overrides, front/back matter. |
| `src/review/api.ts` | Client for the API: fetch structure review, submit overrides, query build status. Base URL from `NEXT_PUBLIC_API_URL`, default `http://localhost:4000/v1`. |
| `package.json`, `tsconfig.json`, `next.config.js` | Workspace config. |

## Rules that bite

- **The API owns no pipeline logic.** If a change makes it compute something about a book,
  it belongs in a stage or a service.
- **Every tenant-scoped read goes through `loadOwned`.** Do not hand-roll a tenant check
  in a route.
- **Every tenant-table query goes through `withTenant`.** Since E4.2, `builds`/`artifacts`/
  `build_stages`/etc. have Row-Level Security keyed on the `app.tenant_id` GUC — a query
  outside `withTenant` sees zero rows (or, worse, fails a write's WITH CHECK) because that
  GUC is unset. Since E5.2 this is also a compile-time property, not just a runtime lint: the
  raw pool is not exported from `db.ts`, so `import { pool }` in a route is a `tsc` error, not
  a gate that has to catch you at CI time. `tools/lint_tenant_scoping.py` (still a CI gate)
  fails if `set_config('app.tenant_id', ...)` or a literal `SET app.tenant_id` appears anywhere
  outside `withTenant`'s own definition in `db.ts`.
- **Every route declares `config: { auth: 'tenant' | 'admin' | 'public' }` (E5.1).** An
  undeclared route defaults to `'tenant'` at request time, but `createApp()` refuses to even
  build the app if any route has no declaration at all — a new route with no `config` fails
  at boot, not by quietly becoming reachable. Don't hand-roll a URL-prefix check in
  `plugins.ts` the way the pre-E5.1 version did; add the declaration to the route instead.
- **Tokens are argon2id hashes, not plaintext (E5.3).** `PUBLISHER_API_TOKENS`/
  `PUBLISHER_ADMIN_TOKENS` never contain a directly usable credential; generate one with
  `npm run hash-token -- <token>`. Never add a code path that logs, echoes, or stores the
  plaintext token anywhere other than the caller's own `Authorization` header.
- **Never route upload bytes through the JSON body parser** — `bodyLimit` is 1 MiB and a
  DOCX is larger.
- **Hardening has tests that must fail against pre-hardening code**:
  `tests/integration/test_api_hardening.py` covers S2/S3/S4 and health;
  `packages/api/src/app.test.ts` covers the full auth matrix + route-coverage +
  fail-closed-startup checks (E0.4/E5.1) via `.inject()`, no DB needed. Read them before
  touching auth, idempotency, or webhook validation.

## Related

`worker.py` (the other side of the queue) · `platform/db/migrations/` (the shared tables,
tenant_id/RLS, least-privilege roles) · `Dockerfile.api`, `docker-compose.yml` · `tests/integration/` ·
`docs/ARCHITECTURE_UPLIFT_PLAN.md` U5/U6/U7 · `docs/ARCHITECTURE_REMEDIATION.md` A2 ·
`docs/ARCHITECTURE_SCORE_10_PLAN.md` E5 (Principal/route-declared auth, compile-time tenant
barrier, tokens hashed at rest).
