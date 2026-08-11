/**
 * Titles + manuscript creation (U6 -- extracted from the monolith; the tenant
 * check now goes through `loadOwned`, which is what makes the previously
 * missing check structurally impossible to omit).
 */
import { randomBytes } from 'node:crypto';
import type { FastifyInstance } from 'fastify';
import { loadOwned, pool } from '../db.js';

export async function registerTitles(server: FastifyInstance): Promise<void> {
  server.post<{ Body: { title: string; author?: string } }>(
    '/v1/titles',
    {
      schema: {
        body: {
          type: 'object',
          required: ['title'],
          properties: {
            title: { type: 'string', minLength: 1 },
            author: { type: 'string' },
          },
        },
      },
    },
    async (request, reply) => {
      const { title, author } = request.body;
      // randomBytes, not Math.random: a guessable resource id is an enumeration vector
      // once these are tenant-scoped.
      const id = `title-${randomBytes(9).toString('base64url')}`;
      const result = await pool.query(
        'INSERT INTO titles (id, tenant_id, title, author) VALUES ($1, $2, $3, $4) RETURNING *',
        [id, request.tenantId, title, author ?? null]
      );
      const record = result.rows[0];
      reply.code(201);
      return { id: record.id, tenantId: record.tenant_id, title: record.title, author: record.author, createdAt: record.created_at };
    }
  );

  server.get<{ Params: { id: string } }>(
    '/v1/titles/:id',
    async (request, reply) => {
      const record = await loadOwned('titles', request.params.id, request.tenantId);
      if (!record) {
        // 404 not 403: another tenant's title must not be distinguishable from a
        // nonexistent one.
        return reply.code(404).send({ error: 'not found' });
      }
      return { id: record.id, tenantId: record.tenant_id, title: record.title, author: record.author, createdAt: record.created_at, status: 'active' };
    }
  );

  server.post<{ Params: { id: string } }>(
    '/v1/titles/:id/manuscripts',
    async (request, reply) => {
      const title = await loadOwned('titles', request.params.id, request.tenantId);
      // The title must belong to the caller before a manuscript can be created
      // under it -- this check was absent in the monolith, so any tenant could
      // attach a manuscript to any title id.
      if (!title) {
        return reply.code(404).send({ error: 'not found' });
      }
      const manuscriptId = `ms-${randomBytes(9).toString('base64url')}`;
      await pool.query(
        'INSERT INTO manuscripts (id, tenant_id, title_id, status) VALUES ($1, $2, $3, $4)',
        [manuscriptId, request.tenantId, title.id, 'uploaded']
      );
      reply.code(201);
      return {
        manuscriptId,
        titleId: title.id,
        status: 'uploaded',
        uploadUrl: `/v1/manuscripts/${manuscriptId}/upload`,
      };
    }
  );
}
