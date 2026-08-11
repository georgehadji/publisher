/**
 * Webhooks (U6 split) + U5/S4 URL validation.
 *
 * S4 is marked "when delivery lands" in the plan, but creation-time validation
 * is the cheapest moment to get it right: delivery does not exist yet, so no
 * production webhook URL exists to break. At creation we require https and
 * reject hosts that resolve to RFC1918 / loopback / link-local / multicast /
 * cloud-metadata addresses. DNS rebinding re-checks must ALSO run at delivery
 * time when delivery lands (the comment below marks the seam).
 */
import { randomBytes } from 'node:crypto';
import { isIP } from 'node:net';
import type { FastifyInstance } from 'fastify';
import { loadOwned, pool } from '../db.js';

function isBlockedIPv4(ip: string): boolean {
  const parts = ip.split('.').map(Number);
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return true;
  const [a, b] = parts;
  if (a === 10) return true; // RFC1918
  if (a === 127) return true; // loopback
  if (a === 169 && b === 254) return true; // link-local incl. 169.254.169.254 metadata
  if (a === 172 && b >= 16 && b <= 31) return true; // RFC1918
  if (a === 192 && b === 168) return true; // RFC1918
  if (a >= 224) return true; // multicast + reserved
  return false;
}

function isBlockedHost(hostname: string): boolean {
  const family = isIP(hostname);
  if (family === 4) return isBlockedIPv4(hostname);
  if (family === 6) {
    const h = hostname.toLowerCase();
    if (h === '::1' || h === '::') return true; // loopback / unspecified
    if (h.startsWith('fc') || h.startsWith('fd')) return true; // ULA
    if (h.startsWith('fe8') || h.startsWith('fe9') || h.startsWith('fea') || h.startsWith('feb')) return true; // link-local
    if (h.startsWith('ff')) return true; // multicast
  }
  return false;
}

/** S4 -- every A/AAAA record for the host must be public. */
async function dnsIsPublic(hostname: string): Promise<boolean> {
  const { lookup } = await import('node:dns/promises');
  const addresses = await lookup(hostname, { all: true, verbatim: true });
  return addresses.every((a) => !isBlockedHost(a.address));
}

export async function registerWebhooks(server: FastifyInstance): Promise<void> {
  server.post<{ Body: { url: string; events: string[] } }>(
    '/v1/webhooks',
    {
      schema: {
        body: {
          type: 'object',
          required: ['url', 'events'],
          properties: {
            url: { type: 'string', format: 'uri' },
            events: {
              type: 'array',
              items: { type: 'string', enum: ['build.completed', 'build.failed', 'gate.awaiting'] },
            },
          },
        },
      },
    },
    async (request, reply) => {
      const { url, events } = request.body;

      // S4 -- https-only and no private/loopback/metadata destinations.
      let parsed: URL;
      try {
        parsed = new URL(url);
      } catch {
        return reply.code(400).send({ error: 'url is not a valid URI' });
      }
      if (parsed.protocol !== 'https:') {
        return reply.code(400).send({ error: 'webhook url must be https' });
      }
      const hostname = parsed.hostname;
      if (isBlockedHost(hostname)) {
        return reply.code(400).send({ error: 'webhook url must not point at a private, loopback, or metadata address' });
      }
      try {
        if (!(await dnsIsPublic(hostname))) {
          return reply.code(400).send({ error: 'webhook host resolves to a private or loopback address' });
        }
      } catch {
        return reply.code(400).send({ error: 'webhook host does not resolve' });
      }
      // NOTE (S4, delivery time): DNS rebinding -- re-resolve the host at
      // delivery time and re-run isBlockedHost before opening the connection.

      const id = `wh-${randomBytes(9).toString('base64url')}`;
      // Webhook signing secret -- MUST be cryptographically random. randomBytes
      // draws from the OS CSPRNG (Math.random would be a ~52-bit forgery vector).
      const secret = `sec-${randomBytes(32).toString('base64url')}`;
      const result = await pool.query(
        'INSERT INTO webhooks (id, tenant_id, url, events, secret) VALUES ($1, $2, $3, $4, $5) RETURNING *',
        [id, request.tenantId, url, JSON.stringify(events), secret]
      );
      const hook = result.rows[0];
      reply.code(201);
      return { id: hook.id, url: hook.url, events: hook.events, secret: hook.secret, createdAt: hook.created_at };
    }
  );

  server.get('/v1/webhooks', async (request) => {
    // Tenant-scoped -- the prior in-memory version listed every tenant's
    // webhooks (including their target URLs) to any authenticated caller.
    const result = await pool.query(
      'SELECT id, url, events, created_at FROM webhooks WHERE tenant_id = $1',
      [request.tenantId]
    );
    return { webhooks: result.rows.map((h) => ({ id: h.id, url: h.url, events: h.events, createdAt: h.created_at })) };
  });
}
