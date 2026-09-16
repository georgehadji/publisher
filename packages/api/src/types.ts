/**
 * Shared Fastify request augmentation (U6 -- the route split made the inline
 * `request.tenantId` assignment a cross-file contract, so it is declared once).
 *
 * E5.1 -- every route declares which auth zone it belongs to via
 * `config: { auth: ... }`. `FastifyContextConfig` is Fastify's documented
 * declaration-merging seam for exactly this: augmenting it here makes
 * `auth` a type-checked field of every route's options object AND of
 * `request.routeOptions.config`, everywhere, with no per-file import needed.
 */
export type AuthRequirement = 'tenant' | 'admin' | 'public';

export type Principal =
  | { kind: 'tenant'; tenantId: string }
  | { kind: 'admin' }
  | { kind: 'public' };

declare module 'fastify' {
  interface FastifyContextConfig {
    /** Which auth zone this route belongs to. Undeclared defaults to the
     * strictest usable zone ('tenant'), and a startup assertion in app.ts
     * refuses to boot if any registered route has no declaration at all. */
    auth?: AuthRequirement;
  }
  interface FastifyRequest {
    /** Resolved by the auth hook for tenant-zone routes. */
    tenantId: string;
    /** Idempotency key reserved by the onRequest hook, filled by onSend (U5/S3). */
    idemKey?: string;
    /** Resolved by the auth hook for EVERY request, regardless of zone --
     * the hook never returns early without assigning one (E5.1). */
    principal: Principal;
  }
  interface FastifyInstance {
    /** Every route registered so far, collected via `onRoute` (E0.4's
     * route-coverage meta-test: a route missing from the auth matrix). */
    publisherRoutes: { method: string; url: string; auth: AuthRequirement | undefined }[];
  }
}
