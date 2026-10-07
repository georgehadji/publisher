/**
 * The structure review's confidence -- read from the AST, never invented.
 *
 * The route used to return `confidence: 1.0` for every chapter and
 * `lowConfidenceNodes: []` for every book, whatever the AST said. These pin the
 * two properties that replaced it: a score is reported as the AST carries it,
 * and "not measured" (null) stays distinct from "measured, nothing low" ([]).
 */
import { describe, expect, it } from 'vitest';
import { LOW_CONFIDENCE_BELOW, orphanedOps, pendingProposals, proposalOp, structureView } from './manuscripts.js';
import { BLOCK_EXCERPT_CHARS } from '../contract.js';
import type { OverrideOp } from '../contract.js';

const para = (text: string) => ({ type: 'paragraph', content: [{ type: 'text', text }] });
const chapter = (n: number, title: string, confidence?: number) => ({
  type: 'chapter',
  sourceRef: { docxId: `chapter:${n}` },
  attrs: { number: n, id: `ch${n}`, title },
  content: [para(`prose of ${title}`)],
  ...(confidence === undefined ? {} : { confidence }),
});

describe('structureView', () => {
  it('reports each chapter confidence as the AST carries it', () => {
    const { chapters } = structureView({
      body: [chapter(1, 'CHAPTER ONE', 0.95), chapter(2, 'NO!', 0.7)],
    });
    expect(chapters.map((c: any) => c.confidence)).toEqual([0.95, 0.7]);
  });

  it('never defaults a missing score to confident', () => {
    const { chapters } = structureView({ body: [chapter(1, 'One')] });
    expect(chapters[0].confidence).toBeNull();
  });

  it('lists every section below the review line, across all three roots', () => {
    const { lowConfidenceNodes } = structureView({
      frontMatter: [{ type: 'titlePage', content: [para('DEDICATION')], confidence: 0.5 }],
      body: [chapter(1, 'CHAPTER ONE', 0.95), chapter(2, 'NO!', 0.7)],
      backMatter: [{ type: 'colophon', content: [para('COLOPHON')], confidence: 0.9 }],
    });
    expect(lowConfidenceNodes).toEqual([
      { root: 'frontMatter', index: 0, type: 'titlePage', title: null, docxId: null,
        text: 'DEDICATION', confidence: 0.5 },
      { root: 'body', index: 1, type: 'chapter', title: 'NO!', docxId: 'chapter:2',
        text: 'prose of NO!', confidence: 0.7 },
    ]);
  });

  it('gives each chapter the id an override op targets it by', () => {
    const { chapters } = structureView({ body: [chapter(1, 'One'), chapter(2, 'Two')] });
    expect(chapters.map((c: any) => c.docxId)).toEqual(['chapter:1', 'chapter:2']);
  });

  it('an untagged node is untargetable, not given a made-up id', () => {
    // An AST from before ingest v4: nothing carries a sourceRef.
    const { chapters } = structureView({
      body: [{ type: 'chapter', attrs: { number: 1, id: 'ch1', title: 'One' }, content: [] }],
    });
    expect(chapters[0].docxId).toBeNull();
  });

  it('every id the view shows is one an op can land on', () => {
    // The view and orphanedOps share one notion of target; if they drifted, a
    // reviewer could aim an op at a shown id and see it reported orphaned.
    const ast = { body: [chapter(1, 'One', 0.5), chapter(2, 'Two', 0.95)] };
    const { chapters, lowConfidenceNodes } = structureView(ast);
    const shown = [...chapters, ...(lowConfidenceNodes ?? [])].map((n: any) => n.docxId);
    expect(shown.every((id: any) => typeof id === 'string')).toBe(true);
    const ops = shown.map((docxId: string, i: number) => op(`ov-${i}`, docxId));
    expect(orphanedOps(ast, ops)).toEqual([]);
  });

  it('the review line is exclusive, as in rules.find_low_confidence', () => {
    const { lowConfidenceNodes } = structureView({
      body: [chapter(1, 'At the line', LOW_CONFIDENCE_BELOW)],
    });
    expect(lowConfidenceNodes).toEqual([]);
  });

  it('an unscored AST is not measured -- null, not an empty list', () => {
    // An AST built before ingest v3 carries no scores. [] would claim it was
    // checked and found clean; it was never checked.
    const { lowConfidenceNodes } = structureView({ body: [chapter(1, 'One'), chapter(2, 'Two')] });
    expect(lowConfidenceNodes).toBeNull();
  });

  it('a scored AST with nothing low is an empty list, not null', () => {
    const { lowConfidenceNodes } = structureView({ body: [chapter(1, 'CHAPTER ONE', 0.95)] });
    expect(lowConfidenceNodes).toEqual([]);
  });

  it('lists the chapters inside a part, naming the part', () => {
    // Only the body's own children were listed, so a book in parts showed none.
    const { chapters } = structureView({ body: [
      { type: 'part', attrs: { title: 'Part I', id: 'pt1' }, content: [chapter(1, 'One')] },
      chapter(2, 'Two'),
    ] });
    expect(chapters.map((c) => [c.title, c.part])).toEqual([['One', 'Part I'], ['Two', null]]);
  });

  it("lists each chapter's blocks: what a split, promote or insert aims at", () => {
    const heading = { type: 'heading', attrs: { level: 2 }, sourceRef: { docxId: 'h1' },
                      content: [{ type: 'text', text: 'A  section' }] };
    const flagged = { ...para('x'.repeat(200)), sourceRef: { docxId: 'p1' },
                      _flags: [{ id: 'ov-f', message: 'unclear', actor: 'u' }] };
    const ast = { body: [{ ...chapter(1, 'One'), content: [heading, flagged] }] };
    const [{ blocks }] = structureView(ast).chapters;
    expect(blocks).toEqual([
      { docxId: 'h1', type: 'heading', level: 2, excerpt: 'A section', flags: [] },
      { docxId: 'p1', type: 'paragraph', level: null, excerpt: 'x'.repeat(BLOCK_EXCERPT_CHARS),
        flags: ['ov-f'] },
    ]);
    expect(orphanedOps(ast, blocks.map((b, i) => op(`ov-${i}`, b.docxId!)))).toEqual([]);
  });
});

// ── orphanedOps: decisions that would have no effect ──────────────────────

const tagged = (type: string, docxId: string, extra: object = {}) => ({
  type, sourceRef: { docxId }, content: [para(docxId)], ...extra,
});
const op = (id: string, docxId: string): OverrideOp => ({
  id, sourceRef: { docxId }, op: 'retitle', value: 'X', actor: 'u', at: '2026-01-01T00:00:00Z',
});

describe('orphanedOps', () => {
  const ast = {
    frontMatter: [{ type: 'titlePage', content: [tagged('paragraph', 'paragraph:front')] }],
    body: [{
      type: 'chapter', attrs: { number: 1, id: 'ch1', title: 'One' },
      sourceRef: { docxId: 'chapter:one' },
      content: [tagged('paragraph', 'paragraph:body')],
    }],
    backMatter: [{ type: 'colophon', content: [tagged('paragraph', 'paragraph:back')] }],
  };

  it('finds a target at any depth, in any root', () => {
    const ops = ['chapter:one', 'paragraph:body', 'paragraph:front', 'paragraph:back']
      .map((ref, i) => op(`ov-${i}`, ref));
    expect(orphanedOps(ast, ops)).toEqual([]);
  });

  it('a node only the effective document has (an inserted break) is no orphan', () => {
    const effective = { body: [{ ...ast.body[0], content: [
      ...ast.body[0].content, { type: 'sceneBreak', sourceRef: { docxId: 'ins-1' } }] }] };
    const deleteBreak = op('ov-del', 'ins-1');
    expect(orphanedOps(ast, [deleteBreak])).toHaveLength(1);
    expect(orphanedOps([ast, effective], [deleteBreak])).toEqual([]);
  });

  it('reports an op whose target is in no node, as overrides/1 names it', () => {
    const lost = op('ov-lost', 'paragraph:gone');
    expect(orphanedOps(ast, [op('ov-ok', 'chapter:one'), lost]))
      .toEqual([{ op: lost, reason: 'no_source_ref' }]);
  });

  it('an AST that tags nothing orphans every op -- ingest before v4', () => {
    // The state every book was in until ingest emitted sourceRefs: every stored
    // op reached resolve and matched nothing, and nothing said so.
    const untagged = { body: [{ type: 'chapter', attrs: { number: 1, id: 'ch1', title: 'One' },
                                content: [para('x')] }] };
    expect(orphanedOps(untagged, [op('ov-1', 'chapter:one')])).toHaveLength(1);
  });

  it('keeps log order', () => {
    const ops = [op('ov-b', 'nope-b'), op('ov-a', 'nope-a')];
    expect(orphanedOps(ast, ops).map((o: any) => o.op.id)).toEqual(['ov-b', 'ov-a']);
  });
});

describe('pendingProposals', () => {
  const document = {
    schema: 'agent-proposal/1',
    agentId: 'structure-propose',
    proposals: [
      { id: 'pr-a', type: 'merge_chapters', sourceRef: { docxId: 'c2' }, rationale: 'prose', confidence: 0.8 },
      { id: 'pr-b', type: 'flag_ambiguity', sourceRef: { docxId: 'c5' }, rationale: 'odd' },
      { id: 'pr-c', type: 'suggest_title', sourceRef: { docxId: 'c6' }, rationale: 'no op for it' },
    ],
  };

  it('lists each proposal with the op accepting it would log', () => {
    expect(pendingProposals(document, [])).toEqual([
      { id: 'pr-a', type: 'merge_chapters', op: 'merge', docxId: 'c2', rationale: 'prose', confidence: 0.8 },
      { id: 'pr-b', type: 'flag_ambiguity', op: 'flag_ambiguity', docxId: 'c5', rationale: 'odd', confidence: null },
    ]);
  });

  it('drops a proposal once its op is in the log', () => {
    const accepted = op('ov-pr-a', 'c2');
    expect(pendingProposals(document, [accepted]).map((p) => p.id)).toEqual(['pr-b']);
  });
});

describe('proposalOp (B2)', () => {
  const at = '2026-10-07T00:00:00.000Z';
  const reclassify = { id: 'pr-r', type: 'reclassify', sourceRef: { docxId: 'p9' }, rationale: 'a heading', from: 'paragraph', to: 'heading' };

  it("carries the parameters a type declares into its op", () => {
    expect(proposalOp(reclassify, 'user:ada', at)).toEqual({
      id: 'ov-pr-r', sourceRef: { docxId: 'p9' }, op: 'reclassify',
      from: 'paragraph', to: 'heading', actor: 'user:ada', at, rationale: 'a heading',
    });
  });

  it('refuses a parameter the type does not declare, rather than trimming it', () => {
    expect(proposalOp({ ...reclassify, value: 2 }, 'user:ada', at)).toBe('a reclassify proposal does not take value');
    expect(proposalOp({ ...reclassify, type: 'merge_chapters' }, 'user:ada', at))
      .toBe('a merge_chapters proposal does not take from, to');
  });

  it('refuses a declared parameter the proposal lacks: nothing validates the op later', () => {
    const { to, ...partial } = reclassify;
    expect(proposalOp(partial, 'user:ada', at)).toBe('a reclassify proposal needs to');
  });

  it('a type with no op, even an Object.prototype name, becomes none', () => {
    expect(proposalOp({ ...reclassify, type: 'suggest_title' }, 'user:ada', at)).toMatch(/has no op/);
    expect(proposalOp({ ...reclassify, type: 'constructor' }, 'user:ada', at)).toMatch(/has no op/);
  });
});
