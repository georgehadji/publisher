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
import { BLOCK_EXCERPT_CHARS, LOW_CONFIDENCE_BELOW, PROPOSAL_OPS, PROPOSAL_PARAMS } from '../contract.js';
import type {
  BlockReview, ChapterReview, OrphanedOp, OverrideOp, ProposalReview, StructureReview,
} from '../contract.js';

export { LOW_CONFIDENCE_BELOW };

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
    { config: { auth: 'review' } },
    async (request, reply) => {
      const manuscript = await loadOwned('manuscripts', request.params.id, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }

      // Structure comes from the manuscript's most recent build that produced an
      // AST: its effective document (overrides applied) when `resolve` ran, else
      // the AST itself. Both from the SAME build -- picking each kind's latest
      // independently could pair one build's AST with another's overrides. No
      // build has necessarily run yet -- report that honestly rather than
      // fabricating chapters that were never inferred.
      const { artifacts, overrides } = await withTenant(request.tenantId, async (client) => ({
        artifacts: await reviewedBuildArtifacts(
          client, request.params.id, ['ast/1', 'doc-effective/1', 'agent-proposal/1']),
        // The override log, in the order it will be applied. Independent of
        // whether a build has run: a reviewer's decisions exist either way.
        overrides: (
          await client.query(
            'SELECT op FROM override_ops WHERE manuscript_id = $1 ORDER BY seq',
            [request.params.id]
          )
        ).rows.map((row) => row.op as OverrideOp),
      }));

      const astRow = artifacts.find((row) => row.schema_id === 'ast/1');
      if (!astRow) {
        return {
          manuscriptId: request.params.id,
          status: 'pending',
          shows: null,
          chapters: [],
          lowConfidenceNodes: null,   // nothing measured yet -- see structureView
          overrides,
          orphanedOps: null,          // no AST yet to check them against
          proposals: null,
        } satisfies StructureReview;
      }

      const ast = JSON.parse(await readCasFile(astRow.sha256));
      const effectiveRow = artifacts.find((row) => row.schema_id === 'doc-effective/1');
      const effective = effectiveRow ? JSON.parse(await readCasFile(effectiveRow.sha256)) : null;
      const proposalRow = artifacts.find((row) => row.schema_id === 'agent-proposal/1');
      return {
        manuscriptId: request.params.id,
        status: 'ready',
        shows: effective ? 'effective' : 'ingested',
        ...structureView(effective ?? ast),
        overrides,
        // An op may aim at a node an earlier op created (a break `insert` added),
        // which only the effective document has.
        orphanedOps: orphanedOps(effective ? [ast, effective] : ast, overrides),
        proposals: proposalRow
          ? pendingProposals(JSON.parse(await readCasFile(proposalRow.sha256)), overrides)
          : null,
      } satisfies StructureReview;
    }
  );

  // ── Proposals ────────────────────────────────────────────────
  //
  // Accepting a model's proposal (W4) is the only way one reaches the override
  // log, and so `resolve` (D9). The op is built here from the stored
  // proposal, never from the request: a caller chooses which proposal, not what
  // it does. Its id is `ov-<proposal id>`, so accepting twice is a 409, and a
  // proposal with a logged op no longer shows as pending.
  server.post<{ Params: { id: string; pid: string }; Body: { actor: string } }>(
    '/v1/manuscripts/:id/proposals/:pid/accept',
    {
      config: { auth: 'review' },
      schema: {
        params: {
          type: 'object',
          properties: { id: { type: 'string' }, pid: { type: 'string', pattern: '^pr-[a-zA-Z0-9_-]+$', maxLength: 64 } },
          required: ['id', 'pid'],
        },
        body: {
          type: 'object',
          required: ['actor'],
          properties: { actor: OVERRIDE_OP_SCHEMA.properties.actor },
          additionalProperties: false,
        },
      },
    },
    async (request, reply) => {
      const { actor } = request.body;
      const principal = request.principal;
      if (principal.kind === 'reviewer' && actor !== `user:${principal.reviewer}`) {
        return reply.code(403).send({ error: `a reviewer accepts as user:${principal.reviewer}` });
      }
      const manuscript = await loadOwned('manuscripts', request.params.id, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }
      const [row] = await withTenant(request.tenantId, (client) =>
        reviewedBuildArtifacts(client, request.params.id, ['agent-proposal/1']));
      const document = row ? JSON.parse(await readCasFile(row.sha256)) : null;
      const proposal = (document?.proposals ?? []).find((p: any) => p.id === request.params.pid);
      if (!proposal) {
        return reply.code(404).send({ error: 'no such proposal in the latest build' });
      }
      const op = proposalOp(proposal, actor, new Date().toISOString());
      if (typeof op === 'string') {
        return reply.code(422).send({ error: op });
      }
      if (await appendOps(request.tenantId, request.params.id, [op]) === 'conflict') {
        return reply.code(409).send({ error: 'this proposal is already accepted' });
      }
      reply.code(201);
      return { documentId: request.params.id, appended: [op.id] };
    }
  );

  // ── Overrides ────────────────────────────────────────────────
  //
  // Appends to the manuscript's override log (platform/db/migrations/004). This
  // used to validate, check tenancy, return `{applied: ops.length}` -- and store
  // nothing, so every reviewer decision was acknowledged and thrown away.
  server.patch<{ Params: { id: string }; Body: { ops: OverrideOp[] } }>(
    '/v1/documents/:id/overrides',
    {
      config: { auth: 'review' },
      schema: {
        body: {
          type: 'object',
          required: ['ops'],
          properties: {
            ops: { type: 'array', minItems: 1, maxItems: 10000, items: OVERRIDE_OP_SCHEMA },
          },
          additionalProperties: false,
        },
      },
    },
    async (request, reply) => {
      const { ops } = request.body;
      // A reviewer writes as themself. The body must name the reviewer the
      // token belongs to: an op attributed to someone else is refused, not
      // rewritten, so what the caller sent is what the log holds.
      const principal = request.principal;
      if (principal.kind === 'reviewer') {
        const actor = `user:${principal.reviewer}`;
        const foreign = ops.filter((op) => op.actor !== actor);
        if (foreign.length > 0) {
          return reply.code(403).send({
            error: `a reviewer's ops carry actor ${actor}; refused: `
              + foreign.map((op) => op.id).join(', '),
          });
        }
      }
      // Every op the schema accepts, `resolve` applies (UNIMPLEMENTED_OPS in
      // publisher_structure.overrides is empty, and overrides.test.ts fails if
      // it is not). An op that does not fit the document it meets is skipped
      // and reported as a warning by the build, never fatal: the log is
      // append-only, and a fatal op would leave the manuscript unbuildable.

      // A "document" is a structured manuscript -- same store, same ownership
      // check as /v1/manuscripts/:id/structure.
      const manuscript = await loadOwned('manuscripts', request.params.id, request.tenantId);
      if (!manuscript) {
        return reply.code(404).send({ error: 'not found' });
      }

      if (await appendOps(request.tenantId, request.params.id, ops) === 'conflict') {
        // An op is immutable once logged. Re-sending one (a retry without an
        // Idempotency-Key, or a reused id) must not silently overwrite it.
        return reply.code(409).send({
          error: 'an override op with this id is already in the log; ops are immutable. '
            + 'Retry with an Idempotency-Key, or send the change as a new op with a new id.',
        });
      }

      reply.code(201);
      return { documentId: request.params.id, appended: ops.map((op) => op.id) };
    }
  );
}

/**
 * The given artifacts of the build a review shows: the manuscript's most recent
 * build that produced an AST. One build for every kind, so a view never pairs
 * one build's AST with another's overrides or proposals.
 */
async function reviewedBuildArtifacts(
  client: { query: (sql: string, params: unknown[]) => Promise<{ rows: any[] }> },
  manuscriptId: string,
  schemaIds: string[],
): Promise<{ schema_id: string; sha256: string }[]> {
  return (
    await client.query(
      `SELECT a.schema_id, a.sha256 FROM artifacts a
       WHERE a.schema_id = ANY($2) AND a.build_id = (
         SELECT x.build_id FROM artifacts x JOIN builds b ON b.id = x.build_id
         WHERE b.document_id = $1 AND x.schema_id = 'ast/1'
         ORDER BY x.created_at DESC LIMIT 1)`,
      [manuscriptId, schemaIds]
    )
  ).rows;
}

/**
 * Appends ops to the log in one transaction (a batch lands whole or not at
 * all), one INSERT per op in order, so `seq` records the order they came in.
 * `conflict`: an op id is already logged, and nothing was written.
 */
async function appendOps(tenantId: string, manuscriptId: string, ops: OverrideOp[]): Promise<'ok' | 'conflict'> {
  try {
    await withTenant(tenantId, async (client) => {
      for (const op of ops) {
        await client.query(
          `INSERT INTO override_ops (manuscript_id, tenant_id, id, op)
           VALUES ($1, $2, $3, $4)`,
          [manuscriptId, tenantId, op.id, JSON.stringify(op)]
        );
      }
    });
    return 'ok';
  } catch (err: any) {
    if (err?.code === '23505') return 'conflict';
    throw err;
  }
}

/**
 * The op accepting `proposal` logs, or why it cannot become one. Its parameters
 * are exactly the ones PROPOSAL_PARAMS declares for its type (B2): one it does
 * not declare is refused rather than trimmed, and one it lacks is refused too,
 * since nothing validates the op on its way into the log.
 */
export function proposalOp(proposal: any, actor: string, at: string): OverrideOp | string {
  if (!Object.hasOwn(PROPOSAL_OPS, proposal.type)) {
    return `proposal type ${proposal.type} has no op to become`;
  }
  const type = proposal.type as keyof typeof PROPOSAL_OPS;
  const declared: readonly string[] = PROPOSAL_PARAMS[type];
  const stray = ['from', 'to', 'value'].filter((name) => name in proposal && !declared.includes(name));
  if (stray.length) {
    return `a ${type} proposal does not take ${stray.join(', ')}`;
  }
  const missing = declared.filter((name) => !(name in proposal));
  if (missing.length) {
    return `a ${type} proposal needs ${missing.join(', ')}`;
  }
  return {
    id: `ov-${proposal.id}`,
    sourceRef: { docxId: proposal.sourceRef.docxId },
    op: PROPOSAL_OPS[type],
    ...Object.fromEntries(declared.map((name) => [name, proposal[name]])),
    actor,
    at,
    rationale: proposal.rationale,
  };
}

/** The proposals no logged op has accepted, in the order the stage wrote them. */
export function pendingProposals(document: any, ops: OverrideOp[]): ProposalReview[] {
  const logged = new Set(ops.map((op) => op.id));
  return (document?.proposals ?? [])
    .filter((p: any) => p.type in PROPOSAL_OPS && !logged.has(`ov-${p.id}`))
    .map((p: any) => ({
      id: p.id,
      type: p.type,
      op: PROPOSAL_OPS[p.type as keyof typeof PROPOSAL_OPS],
      docxId: p.sourceRef.docxId,
      rationale: p.rationale,
      confidence: typeof p.confidence === 'number' ? p.confidence : null,
    }));
}

/**
 * One `overrides/1` op, verbatim from schemas/overrides/overrides.schema.json
 * `$defs.overrideOp`. Inlined because the API image does not ship `schemas/`;
 * manuscripts.test.ts fails if this and the schema file differ at all.
 */
export const OVERRIDE_OP_SCHEMA = {
  type: 'object',
  properties: {
    id: { type: 'string', pattern: '^ov-[a-zA-Z0-9_-]+$', maxLength: 64 },
    sourceRef: {
      type: 'object',
      properties: {
        docxId: { type: 'string', maxLength: 128 },
        contentHash: { type: 'string', pattern: '^[a-f0-9]{64}$' },
        fallbackText: { type: 'string', maxLength: 256 },
      },
      required: ['docxId'],
      additionalProperties: false,
    },
    op: {
      type: 'string',
      $comment: '`rename` was dropped: it meant nothing `retitle` does not, and the API refused it (422) from the day the log existed, so no stored log holds one.',
      enum: [
        'reclassify', 'split', 'merge', 'promote', 'demote', 'delete',
        'insert', 'retitle', 'set_attr', 'flag_ambiguity', 'resolve_ambiguity', 'start_body',
        'end_body',
      ],
    },
    path: { type: 'string', maxLength: 256, description: 'JSON Pointer to the target within the AST' },
    from: { type: 'string', maxLength: 64 },
    to: { type: 'string', maxLength: 64 },
    value: {},
    rationale: { type: 'string', maxLength: 4096 },
    actor: { type: 'string', maxLength: 64 },
    at: { type: 'string', format: 'date-time' },
  },
  required: ['id', 'sourceRef', 'op', 'actor', 'at'],
  additionalProperties: false,
  allOf: [
    { if: { properties: { op: { const: 'reclassify' } } }, then: { required: ['from', 'to'] } },
    {
      $comment: 'Reclassifying to a heading may set its level in `value` (B3).',
      if: { properties: { op: { const: 'reclassify' }, to: { const: 'heading' } }, required: ['to'] },
      then: { properties: { value: { type: 'integer', minimum: 1, maximum: 6 } } },
    },
    { if: { properties: { op: { const: 'retitle' } } }, then: { required: ['value'] } },
    { if: { properties: { op: { const: 'flag_ambiguity' } } }, then: { required: ['rationale'] } },
    { if: { properties: { op: { const: 'set_attr' } } }, then: { required: ['path'] } },
    { if: { properties: { op: { const: 'insert' } } }, then: { required: ['value'] } },
  ],
} as const;



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
 * The id an override op must carry to target `node`, or `null` when nothing can
 * target it. The one definition of "target" -- `structureView` exposes these and
 * `orphanedOps` matches against them, so an id the view shows is never reported
 * orphaned. Mirrors publisher_structure.overrides `_matches`: `sourceRef.docxId`,
 * a bare-string sourceRef, or sourceRefLink.
 */
function docxIdOf(node: any): string | null {
  const src = node?.sourceRef ?? node?.sourceRefLink;
  if (typeof src === 'string') return src;
  return typeof src?.docxId === 'string' ? src.docxId : null;
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
 *
 * Each entry carries `docxId`, the id an override op aims at -- without it a
 * reviewer could see a doubtful chapter and have no way to address it. `null`
 * means untargetable: a front/back-matter section wrapper (the schema gives it
 * no sourceRef; its contents have one), or any node of an AST from before
 * ingest v4.
 */
export function structureView(ast: any): Pick<StructureReview, 'chapters' | 'lowConfidenceNodes'> {
  // Chapters inside a part count too: listing only the body's own children
  // left every chapter of a book divided into parts out of the review.
  const inBody = (ast?.body ?? []).flatMap((node: any) =>
    node?.type === 'part'
      ? (node.content ?? []).map((chapter: any) => ({ chapter, part: node.attrs?.title ?? null }))
      : [{ chapter: node, part: null }]
  );
  const chapters = inBody
    .filter(({ chapter }: any) => chapter?.type === 'chapter')
    .map(({ chapter, part }: any): ChapterReview => ({
      number: chapter.attrs?.number ?? null,
      title: chapter.attrs?.title ?? '',
      part,
      docxId: docxIdOf(chapter),
      confidence: typeof chapter.confidence === 'number' ? chapter.confidence : null,
      flags: flagsOf(chapter),
      blocks: (Array.isArray(chapter.content) ? chapter.content : []).map(blockView),
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
          docxId: docxIdOf(node),
          text: firstText(node).slice(0, 100),
          confidence: node.confidence,
        }))
    : null;

  return { chapters, lowConfidenceNodes };
}

/** The ids of the `flag_ambiguity` ops standing on a node of the effective document. */
function flagsOf(node: any): string[] {
  return (Array.isArray(node?._flags) ? node._flags : [])
    .map((flag: any) => flag?.id)
    .filter((id: unknown): id is string => typeof id === 'string');
}

function blockView(node: any): BlockReview {
  return {
    docxId: docxIdOf(node),
    type: node?.type ?? 'unknown',
    level: node?.type === 'heading' && typeof node.attrs?.level === 'number' ? node.attrs.level : null,
    excerpt: allText(node).slice(0, BLOCK_EXCERPT_CHARS),
    flags: flagsOf(node),
  };
}

/** A node's text, runs joined, whitespace collapsed -- what a reviewer reads. */
function allText(node: any): string {
  const parts: string[] = [];
  const walk = (n: any): void => {
    if (n?.type === 'text') parts.push(n.text ?? '');
    for (const child of Array.isArray(n?.content) ? n.content : []) walk(child);
  };
  walk(node);
  return parts.join('').replace(/\s+/g, ' ').trim();
}

/**
 * Stored override ops that target no node in `ast` -- `overrides/1`'s
 * `orphanedOps`, reason `no_source_ref`.
 *
 * `resolve` applies an op only where some node's `sourceRef.docxId` equals the
 * op's, and skips one that matches nothing without a word: no error, no warning,
 * no metric. So a reviewer's decision could sit in the log having no effect on
 * the book while every surface reported it recorded. This is where it shows.
 * Not fatal on purpose: the log is append-only, so failing the build on an
 * orphan would leave the manuscript unbuildable for good.
 *
 * Mirrors publisher_structure.overrides `_matches` and `_CHILD_KEYS` exactly --
 * any node reached through content/frontMatter/backMatter/body, matched on
 * `docxIdOf` -- so an op is reported orphaned here exactly when resolve would
 * skip it.
 */
export function orphanedOps(ast: any, ops: OverrideOp[]): OrphanedOp[] {
  // `ast` may be a list of documents: an op is an orphan when none holds its node.
  const ids = new Set<string>();
  const walk = (node: any): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (!node || typeof node !== 'object') return;
    const id = docxIdOf(node);
    if (id !== null) ids.add(id);
    for (const key of ['content', 'frontMatter', 'backMatter', 'body']) walk(node[key]);
  };
  walk(ast);
  return ops
    .filter((op) => !ids.has(op?.sourceRef?.docxId))
    .map((op) => ({ op, reason: 'no_source_ref' as const }));
}
