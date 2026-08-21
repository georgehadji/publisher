---
name: publisher-packages
description: Map of the `packages/` folder — the TypeScript surfaces: `packages/api` (Fastify + Postgres REST API — titles, manuscripts, uploads, builds, SSE, artifacts, webhooks, health, admin metrics) and `packages/web` (Next.js structure-review UI). Use this whenever a task touches an HTTP route, auth/tenancy, idempotency, rate limiting, artifact download, build status or SSE events, webhook validation, or the review UI. Read before editing anything under packages/.
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
| `src/db.ts` | **The single data-access layer.** Everything touching Postgres or the CAS filesystem lives here so routes stay thin. `loadOwned` centralizes the tenant-ownership check against a table whitelist (never interpolated input) — the one place that check was originally missing was manuscript creation, which a single helper makes structurally impossible to omit. Also `casPath` (validates `^[a-f0-9]{64}$` before `path.join` — S2/N5), `readCasFile` (size ceiling — S6/N4), `casBlobExists`, `DELIVERABLE_SCHEMAS`, `CAS_ROOT`, `pool`. |
| `src/plugins.ts` | Cross-cutting Fastify plugins registered as one unit so the `onRequest` hooks run in a fixed order: **auth + tenancy (S1) → idempotency (S3) → tenant-keyed rate limit (S7)**, plus helmet security headers (S5) and strict CORS. Exports `isAdminToken`. |
| `src/types.ts` | Fastify request augmentation — `request.tenantId` (set by the auth hook) and `request.idemKey`. Declared once because the route split made it a cross-file contract. |
| `src/routes/titles.ts` | `POST/GET /v1/titles`, `GET /v1/titles/:id`, `POST /v1/titles/:id/manuscripts`. |
| `src/routes/manuscripts.ts` | `PUT /v1/manuscripts/:id/upload` — streams the body straight into the shared CAS (never through the 1 MiB JSON body parser), records `manuscripts.source_sha256`, checks the DOCX/ZIP `PK` magic bytes. Also `GET /v1/manuscripts/:id/structure` and `/v1/documents/:id/overrides`. |
| `src/routes/builds.ts` | `POST/GET /v1/builds`, `GET /v1/builds/:id`, `GET /v1/builds/:id/preflight`, and `GET /v1/builds/:id/events` (SSE). The SSE endpoint **LISTENs on a dedicated connection** for the worker's `NOTIFY build_<id>` instead of polling two queries every 500 ms per client (N3); wire format `event: stage.progress` / `build.completed|failed` is unchanged. |
| `src/routes/artifacts.ts` | `GET /v1/builds/:id/artifacts/:kind` (metadata) and `.../download` (byte streaming off the CAS). |
| `src/routes/webhooks.ts` | `POST/GET /v1/webhooks` with S4 URL validation at creation: https only; rejects hosts resolving to RFC1918, loopback, link-local, multicast, or cloud-metadata addresses. **DNS-rebinding re-checks must also run at delivery time** — the seam is marked in the file. |
| `src/routes/health.ts` | `GET /v1/health` — actually checks Postgres (short timeout) and CAS readability, returning 503 with a per-check breakdown. It used to return unconditional 200 `ok`, so a load balancer happily routed to an instance whose database was gone. |
| `src/routes/admin.ts` | `GET /v1/admin/metrics` — stage-level p50/p95 from `build_stages.duration_ms`, data that was always collected and never read. Admin-token gated via `PUBLISHER_ADMIN_TOKENS`; unconfigured means the route is effectively absent. |
| `package.json`, `tsconfig.json` | `tsconfig.json` **extends the monorepo root** — `Dockerfile.api` must preserve the `repo-root/packages/api/` layout or `../../tsconfig.json` resolves to nothing. |

### Environment
`DATABASE_URL`, `PORT`, `HOST`, `PUBLISHER_API_TOKENS` (`token:tenant`), `PUBLISHER_CORS_ORIGINS`,
`PUBLISHER_CAS_ROOT`, `PUBLISHER_ADMIN_TOKENS`. The API is both a CAS **reader** (artifact
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
- **Never route upload bytes through the JSON body parser** — `bodyLimit` is 1 MiB and a
  DOCX is larger.
- **Hardening has tests that must fail against pre-hardening code**:
  `tests/integration/test_api_hardening.py` covers S2/S3/S4 and health. Read it before
  touching auth, idempotency, or webhook validation.

## Related

`worker.py` (the other side of the queue) · `platform/db/schema.sql` (the shared tables) ·
`Dockerfile.api`, `docker-compose.yml` · `tests/integration/` ·
`docs/ARCHITECTURE_UPLIFT_PLAN.md` U5/U6/U7 · `docs/ARCHITECTURE_REMEDIATION.md` A2.
