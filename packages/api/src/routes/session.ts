/**
 * `GET /v1/whoami` -- who a bearer token says the caller is (W2,
 * docs/WIRING_PLAN.md). The review UI's sign-in checks a reviewer's token
 * here, and its Server Actions read the reviewer's name from it to attribute
 * an op: the API refuses a reviewer's op whose actor is anyone else.
 */
import type { FastifyInstance } from 'fastify';

export interface WhoAmI {
  kind: 'tenant' | 'reviewer';
  tenantId: string;
  /** The reviewer's name; `null` for a tenant (service) token. */
  reviewer: string | null;
}

export async function registerSession(server: FastifyInstance): Promise<void> {
  server.get('/v1/whoami', { config: { auth: 'review' } }, async (request): Promise<WhoAmI> => {
    const principal = request.principal;
    return principal.kind === 'reviewer'
      ? { kind: 'reviewer', tenantId: principal.tenantId, reviewer: principal.reviewer }
      : { kind: 'tenant', tenantId: request.tenantId, reviewer: null };
  });
}
