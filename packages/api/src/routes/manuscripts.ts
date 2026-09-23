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
import { CAS_ROOT, UPLOAD_MAX_BYTES, casPath, loadOwned, readCasFile, withTenant } from '../db.js';

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
      // Legacy binary Word. The worker converts it to .docx with LibreOffice
      // before anything parses it (stages/ingest_stage.py `_convert_legacy_doc`).
      'application/msword',
      'application/octet-stream',
      'application/zip',
    ],
    { bodyLimit: UPLOAD_MAX_BYTES },
    (_req, payload, done) => done(null, payload)
  );

  server.put<{ Params: { id: string } }>(
    '/v1/manuscripts/:id/upload',
    { bodyLimit: UPLOAD_MAX_BYTES, config: { auth: 'tenant' } },
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

      // A manuscript is either a DOCX (ZIP, "PK") or a legacy binary .doc
      // (OLE2 compound file, D0 CF 11 E0 A1 B1 1A E1). The worker converts the
      // latter with LibreOffice before parsing; anything else is neither.
      const head = Buffer.alloc(8);
      const fh = await open(tmp, 'r');
      try {
        await fh.read(head, 0, 8, 0);
      } finally {
        await fh.close();
      }
      const isDocx = head[0] === 0x50 && head[1] === 0x4b;
      const isLegacyDoc = head.subarray(0, 8).equals(
        Buffer.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1])
      );
      if (!isDocx && !isLegacyDoc) {
        await unlink(tmp).catch(() => {});
        return reply.code(400).send({
          error: 'uploaded bytes are neither a DOCX (ZIP magic) nor a legacy .doc (OLE2 magic).',
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
      await withTenant(request.tenantId, (client) =>
        client.query(
          'UPDATE manuscripts SET source_sha256 = $1, source_size = $2, source_media_type = $3 WHERE id = $4',
          [sha, size, mediaType, id]
        )
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
    { config: { auth: 'tenant' } },
    async (request, reply) => {
      const manuscript = await loadOwned('manuscripts', request.params.id, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }

      // Structure comes from the `ast` artifact of the manuscript's most recent
      // build. No build has necessarily run yet -- report that honestly rather
      // than fabricating chapters that were never inferred.
      const artifact = (
        await withTenant(request.tenantId, (client) =>
          client.query(
            `SELECT a.sha256 FROM artifacts a
             JOIN builds b ON b.id = a.build_id
             WHERE b.document_id = $1 AND a.schema_id = 'ast/1'
             ORDER BY a.created_at DESC LIMIT 1`,
            [request.params.id]
          )
        )
      ).rows[0];

      if (!artifact) {
        return {
          manuscriptId: request.params.id,
          status: 'pending',
          chapters: [],
          lowConfidenceNodes: null,   // nothing measured yet -- see structureView
          overrides: [],
        };
      }

      const ast = JSON.parse(await readCasFile(artifact.sha256));
      return {
        manuscriptId: request.params.id,
        status: 'ready',
        ...structureView(ast),
        overrides: [],
      };
    }
  );

  // ── Overrides ────────────────────────────────────────────────
  server.patch<{ Params: { id: string }; Body: { ops: unknown[] } }>(
    '/v1/documents/:id/overrides',
    {
      config: { auth: 'tenant' },
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

/**
 * Below this, a structural decision goes to review. The same line
 * `publisher_structure.rules.find_low_confidence` and ingest's scores are set
 * against (LLM_STRATEGY.md §5).
 */
export const LOW_CONFIDENCE_BELOW = 0.8;

const SECTION_ROOTS = ['frontMatter', 'body', 'backMatter'] as const;

function firstText(node: any): string {
  if (node?.type === 'text') return node.text ?? '';
  for (const child of Array.isArray(node?.content) ? node.content : []) {
    const text = firstText(child);
    if (text) return text;
  }
  return '';
}

/**
 * The structure review's view of an `ast/1` document.
 *
 * Confidence is reported as the AST carries it, never defaulted. It used to be a
 * literal 1.0 for every chapter, beside a literal `lowConfidenceNodes: []` -- so
 * every book reviewed as certain, because nothing read the scores ingest now
 * records.
 *
 * `lowConfidenceNodes` is `null`, not `[]`, when the AST carries no scores at
 * all (one built before ingest v3): "not measured" and "measured, nothing low"
 * are different answers, and collapsing them is what certified unmeasured books
 * as clean on the composition path too.
 */
export function structureView(ast: any) {
  const chapters = (ast?.body ?? [])
    .filter((node: any) => node?.type === 'chapter')
    .map((node: any) => ({
      number: node.attrs?.number ?? null,
      title: node.attrs?.title ?? '',
      confidence: typeof node.confidence === 'number' ? node.confidence : null,
    }));

  const sections = SECTION_ROOTS.flatMap((root) =>
    (Array.isArray(ast?.[root]) ? ast[root] : []).map((node: any, index: number) => ({
      root,
      index,
      node,
    }))
  );
  const scored = sections.some(({ node }) => typeof node?.confidence === 'number');

  const lowConfidenceNodes = scored
    ? sections
        .filter(({ node }) => typeof node.confidence === 'number'
          && node.confidence < LOW_CONFIDENCE_BELOW)
        .map(({ root, index, node }) => ({
          root,
          index,
          type: node.type,
          title: node.attrs?.title ?? null,
          text: firstText(node).slice(0, 100),
          confidence: node.confidence,
        }))
    : null;

  return { chapters, lowConfidenceNodes };
}
