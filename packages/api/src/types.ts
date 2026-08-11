/**
 * Shared Fastify request augmentation (U6 -- the route split made the inline
 * `request.tenantId` assignment a cross-file contract, so it is declared once).
 */
declare module 'fastify' {
  interface FastifyRequest {
    /** Resolved by the auth hook for authenticated routes. */
    tenantId: string;
    /** Idempotency key reserved by the onRequest hook, filled by onSend (U5/S3). */
    idemKey?: string;
  }
}

export {};
