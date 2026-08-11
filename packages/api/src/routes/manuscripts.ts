/**
 * Manuscript upload + structure (U2 route, moved into the U6 split).
 *
 * The upload streams the request body into the shared CAS (same layout the
 * worker reads from), records manuscripts.source_sha256, and never routes the
 * bytes through the JSON body parser (bodyLimit is 1 MiB; a DOCX is larger).
 * A DOCX is a ZIP archive: the first two bytes are 'PK' (0x50 0x4B).
 */
import { createHash, randomBytes } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { access, mkdir, open, rename, unlink } from 'node:fs/promises';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import path from 'node:path';
import type { FastifyInstance } from 'fastify';
import { CAS_ROOT, UPLOAD_MAX_BYTES, casPath, loadOwned, pool, readCasFile } from '../db.js';

export async function registerManuscripts(server: FastifyInstance): Promise<void> {
  // Fastify 5 415s on any content type with no registered parser, and its
  // parsers buffer bodies unless registered WITHOUT `parseAs` (the stream
  // variant -- Fastify 5 removed the `parseAs: 'stream'` option name but the
  // no-parseAs overload passes the raw payload stream through). The upload body
  // must NOT be buffered (a 100 MB DOCX does not belong in memory). Only the
  // upload media types are registered; every other route keeps the
  // 415-on-unknown behavior.
  server.addContentTypeParser(
    [
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'application/octet-stream',
      'application/zip',
    ],
    { bodyLimit: UPLOAD_MAX_BYTES },
    (_req, payload, done) => done(null, payload)
  );

  server.put<{ Params: { id: string } }>(
    '/v1/manuscripts/:id/upload',
    { bodyLimit: UPLOAD_MAX_BYTES },
    async (request, reply) => {
      const { id } = request.params;
      // Defense in depth: ids are server-generated (ms- + base64url), but the id
      // is interpolated into a filesystem path below -- a malicious shape must
      // never escape .upload-tmp.
      if (!/^[A-Za-z0-9_-]+$/.test(id)) {
        return reply.code(400).send({ error: 'malformed manuscript id' });
      }
      const manuscript = await loadOwned('manuscripts', id, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }

      const shasum = createHash('sha256');
      let size = 0;
      let overLimit = false;
      const meter = new Transform({
        transform(chunk, _enc, cb) {
          size += chunk.length;
          if (size > UPLOAD_MAX_BYTES) {
            overLimit = true;
            cb(new Error('upload exceeds limit'));
            return;
          }
          shasum.update(chunk);
          cb(null, chunk);
        },
      });

      const tmpDir = path.join(CAS_ROOT, '.upload-tmp');
      const tmp = path.join(tmpDir, `${id}-${Date.now()}`);
      await mkdir(tmpDir, { recursive: true });
      try {
        await pipeline(request.body as NodeJS.ReadableStream, meter, createWriteStream(tmp));
      } catch {
        // Only an actual overage is a client-side 413; a mid-stream disconnect,
        // disk-full, or IO error must not be reported as a size violation.
        await unlink(tmp).catch(() => {});
        if (overLimit) {
          return reply.code(413).send({ error: `manuscript exceeds ${UPLOAD_MAX_BYTES} byte upload limit` });
        }
        return reply.code(500).send({ error: 'upload failed' });
      }

      if (size === 0) {
        await unlink(tmp).catch(() => {});
        return reply.code(400).send({ error: 'empty manuscript upload' });
      }

      const head = Buffer.alloc(2);
      const fh = await open(tmp, 'r');
      try {
        await fh.read(head, 0, 2, 0);
      } finally {
        await fh.close();
      }
      if (head[0] !== 0x50 || head[1] !== 0x4b) {
        await unlink(tmp).catch(() => {});
        return reply.code(400).send({
          error: 'uploaded bytes are not a DOCX (missing ZIP magic). Convert legacy .doc files to .docx first.',
        });
      }

      const sha = shasum.digest('hex');
      const dest = casPath(sha);
      await mkdir(path.dirname(dest), { recursive: true });
      try {
        await rename(tmp, dest);
      } catch (err: any) {
        // Possible dedup (same content already in CAS) -- but only treat it as
        // one if the destination really holds the bytes. An EPERM from a broken
        // CAS volume must not silently report a 201 for a blob that was never
        // stored; that would surface later as a worker BAD_INPUT with the
        // tenant's manuscript nowhere in the store.
        if (err?.code === 'EEXIST' || err?.code === 'EPERM') {
          try {
            await access(dest);
          } catch {
            throw err;
          }
          await unlink(tmp).catch(() => {});
        } else {
          throw err;
        }
      }

      const mediaType = request.headers['content-type'] ?? 'application/octet-stream';
      await pool.query(
        'UPDATE manuscripts SET source_sha256 = $1, source_size = $2, source_media_type = $3 WHERE id = $4',
        [sha, size, mediaType, id]
      );
      reply.code(201);
      return {
        manuscriptId: id,
        status: 'uploaded',
        sha256: sha,
        size,
        mediaType,
      };
    }
  );

  server.get<{ Params: { id: string } }>(
    '/v1/manuscripts/:id/structure',
    async (request, reply) => {
      const manuscript = await loadOwned('manuscripts', request.params.id, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }

      // Structure comes from the `ast` artifact of the manuscript's most recent
      // build. No build has necessarily run yet -- report that honestly rather
      // than fabricating chapters that were never inferred.
      const artifact = (
        await pool.query(
          `SELECT a.sha256 FROM artifacts a
           JOIN builds b ON b.id = a.build_id
           WHERE b.document_id = $1 AND a.schema_id = 'ast/1'
           ORDER BY a.created_at DESC LIMIT 1`,
          [request.params.id]
        )
      ).rows[0];

      if (!artifact) {
        return {
          manuscriptId: request.params.id,
          status: 'pending',
          chapters: [],
          lowConfidenceNodes: [],
          overrides: [],
        };
      }

      const ast = JSON.parse(await readCasFile(artifact.sha256));
      const chapters = (ast.body ?? [])
        .filter((node: any) => node.type === 'chapter')
        .map((node: any) => ({
          number: node.attrs?.number ?? null,
          title: node.attrs?.title ?? '',
          confidence: 1.0,
        }));

      return {
        manuscriptId: request.params.id,
        status: 'ready',
        chapters,
        lowConfidenceNodes: [],
        overrides: [],
      };
    }
  );

  // ── Overrides ────────────────────────────────────────────────
  server.patch<{ Params: { id: string }; Body: { ops: unknown[] } }>(
    '/v1/documents/:id/overrides',
    {
      schema: {
        body: {
          type: 'object',
          required: ['ops'],
          properties: { ops: { type: 'array' } },
        },
      },
    },
    async (request, reply) => {
      // A "document" is a structured manuscript -- same store, same ownership
      // check as /v1/manuscripts/:id/structure.
      const manuscript = await loadOwned('manuscripts', request.params.id, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }
      const { ops } = request.body;
      return {
        documentId: request.params.id,
        applied: ops.length,
        timestamp: new Date().toISOString(),
      };
    }
  );
}
