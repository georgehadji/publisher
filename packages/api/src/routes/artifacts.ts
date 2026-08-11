/**
 * Artifact delivery (U6 split): metadata + byte streaming off the CAS.
 */
import { createReadStream } from 'node:fs';
import type { FastifyInstance } from 'fastify';
import { DELIVERABLE_SCHEMAS, casBlobExists, casPath, loadOwned, pool } from '../db.js';

export async function registerArtifacts(server: FastifyInstance): Promise<void> {
  server.get<{ Params: { id: string; kind: string } }>(
    '/v1/builds/:id/artifacts/:kind',
    async (request, reply) => {
      const { id, kind } = request.params;
      const build = await loadOwned('builds', id, request.tenantId);
      if (!build) {
        return reply.code(404).send({ error: 'not found' });
      }
      const schemaId = DELIVERABLE_SCHEMAS[kind];
      if (!schemaId) {
        return reply.code(404).send({
          error: `unknown artifact kind '${kind}'`,
          available: Object.keys(DELIVERABLE_SCHEMAS),
        });
      }
      const artifact = (
        await pool.query('SELECT * FROM artifacts WHERE build_id = $1 AND schema_id = $2', [
          id,
          schemaId,
        ])
      ).rows[0];
      if (!artifact) {
        return reply.code(404).send({ error: 'artifact not found -- this stage has not produced output yet' });
      }
      return {
        buildId: id,
        artifactKind: kind,
        schemaId: artifact.schema_id,
        sha256: artifact.sha256,
        mediaType: artifact.media_type,
        size: Number(artifact.size),
        downloadUrl: `/v1/builds/${id}/artifacts/${kind}/download`,
      };
    }
  );

  server.get<{ Params: { id: string; kind: string } }>(
    '/v1/builds/:id/artifacts/:kind/download',
    async (request, reply) => {
      const { id, kind } = request.params;
      const build = await loadOwned('builds', id, request.tenantId);
      if (!build) {
        return reply.code(404).send({ error: 'not found' });
      }
      const schemaId = DELIVERABLE_SCHEMAS[kind];
      if (!schemaId) {
        return reply.code(404).send({
          error: `unknown artifact kind '${kind}'`,
          available: Object.keys(DELIVERABLE_SCHEMAS),
        });
      }
      const artifact = (
        await pool.query('SELECT * FROM artifacts WHERE build_id = $1 AND schema_id = $2', [
          id,
          schemaId,
        ])
      ).rows[0];
      if (!artifact) {
        return reply.code(404).send({ error: 'artifact not found' });
      }
      // A recorded artifact whose blob is absent means the API and the worker
      // are not looking at the same CAS. Streaming it raised a raw ENOENT,
      // which Fastify turned into a 500 quoting the server's filesystem path
      // back to the caller. Report the condition without leaking where the
      // store lives. casPath also validates the hash shape (S2).
      if (!(await casBlobExists(artifact.sha256))) {
        request.log.error({ sha256: artifact.sha256 }, 'artifact blob missing from CAS');
        return reply.code(404).send({
          error: 'artifact bytes are not available in this deployment’s content store',
          sha256: artifact.sha256,
        });
      }
      reply.type(artifact.media_type);
      return reply.send(createReadStream(casPath(artifact.sha256)));
    }
  );
}
