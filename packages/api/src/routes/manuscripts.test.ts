/**
 * The structure review's confidence -- read from the AST, never invented.
 *
 * The route used to return `confidence: 1.0` for every chapter and
 * `lowConfidenceNodes: []` for every book, whatever the AST said. These pin the
 * two properties that replaced it: a score is reported as the AST carries it,
 * and "not measured" (null) stays distinct from "measured, nothing low" ([]).
 */
import { describe, expect, it } from 'vitest';
import { LOW_CONFIDENCE_BELOW, fitOutcomes, layoutProposals, orphanedOps, pendingProposals, proposalOp, structureView } from './manuscripts.js';
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
      { id: 'pr-a', type: 'merge_chapters', op: 'merge', docxId: 'c2', rationale: 'prose', confidence: 0.8, page: null, kind: null },
      { id: 'pr-b', type: 'flag_ambiguity', op: 'flag_ambiguity', docxId: 'c5', rationale: 'odd', confidence: null, page: null, kind: null },
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
    expect(proposalOp({ ...reclassify, type: 'merge_chapters' }, 'user:ada', at))
      .toBe('a merge_chapters proposal does not take from, to');
    expect(proposalOp({ ...reclassify, type: 'split_chapter', value: 2 }, 'user:ada', at))
      .toBe('a split_chapter proposal does not take from, to, value');
  });

  it("carries an optional parameter only when the proposal has it (B8)", () => {
    const withLevel = proposalOp({ ...reclassify, value: 2 }, 'user:ada', at) as OverrideOp;
    expect(withLevel.value).toBe(2);
    expect('value' in (proposalOp(reclassify, 'user:ada', at) as OverrideOp)).toBe(false);
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

describe('layout-review proposals (K4)', () => {
  const at = '2026-10-14T00:00:00.000Z';
  const fit = (id: string, docxId: string, value: string) => ({ id, type: 'fit_paragraph', docxId, value });
  const review = {
    schema: 'layout-review/1',
    agentId: 'compositor',
    findings: [
      { kind: 'widow', page: 5, verdict: 'confirmed', proposals: [fit('pr-w', 'p7', 'tight')] },
      { kind: 'runt', page: 5, verdict: 'confirmed', proposals: [fit('pr-w', 'p7', 'tight'), fit('pr-r', 'p8', 'tighter')] },
      { kind: 'river', page: 9, verdict: 'confirmed' },
    ],
  };

  it("takes agent-proposal/1's shape, once per change, the rationale derived from the finding", () => {
    const proposals = layoutProposals(review);
    expect(proposals.map((p) => [p.id, p.sourceRef.docxId, p.value, p.page, p.kind])).toEqual([
      ['pr-w', 'p7', 'tight', 5, 'widow'], ['pr-r', 'p8', 'tighter', 5, 'runt']]);
    expect(proposals[0].rationale).toBe("A widow on page 5: set its paragraph's fit to tight to pull the line back.");
  });

  it('accepting one logs set_attr on /attrs/fit: the path the log requires, the value the rule chose', () => {
    expect(proposalOp(layoutProposals(review)[0], 'user:ada', at)).toEqual({
      id: 'ov-pr-w', sourceRef: { docxId: 'p7' }, op: 'set_attr', path: '/attrs/fit', value: 'tight',
      actor: 'user:ada', at, rationale: "A widow on page 5: set its paragraph's fit to tight to pull the line back.",
    });
  });

  it('shows its page and kind for review, and leaves once accepted', () => {
    const pending = pendingProposals({ proposals: layoutProposals(review) }, [op('ov-pr-w', 'p7')]);
    expect(pending).toEqual([{ id: 'pr-r', type: 'fit_paragraph', op: 'set_attr', docxId: 'p8',
      rationale: "A runt on page 5: set its paragraph's fit to tighter to pull the line back.",
      confidence: null, page: 5, kind: 'runt' }]);
  });

  it('a structure proposal never gains a path', () => {
    const merge = { id: 'pr-m', type: 'merge_chapters', sourceRef: { docxId: 'c2' }, rationale: 'prose' };
    expect('path' in (proposalOp(merge, 'user:ada', at) as OverrideOp)).toBe(false);
  });
});

describe('fit outcomes (K5)', () => {
  const review = (fitOutcomes?: unknown) =>
    ({ schema: 'layout-review/1', agentId: 'compositor', findings: [], ...(fitOutcomes ? { fitOutcomes } : {}) });

  it('is null with no layout review: not checked, which is not "no fits"', () => {
    expect(fitOutcomes(null)).toBeNull();
  });

  it('is empty when the review ran and no paragraph carries a fit (the stage omits the key)', () => {
    expect(fitOutcomes(review())).toEqual([]);
  });

  it('passes each outcome through, in the order the stage wrote them', () => {
    expect(fitOutcomes(review([
      { docxId: 'p7', fit: 'tight', outcome: 'cleared' },
      { docxId: 'p8', fit: 'tighter', outcome: 'still-defective' },
      { docxId: 'p9', fit: 'tight', outcome: 'unmeasured' },
    ]))).toEqual([
      { docxId: 'p7', fit: 'tight', outcome: 'cleared' },
      { docxId: 'p8', fit: 'tighter', outcome: 'still-defective' },
      { docxId: 'p9', fit: 'tight', outcome: 'unmeasured' },
    ]);
  });

  it('drops a row it cannot vouch for rather than putting it on the wire', () => {
    expect(fitOutcomes(review([
      { docxId: 'p7', fit: 'tight', outcome: 'not-flagged' },
      { docxId: 'p8', fit: 'squeeze', outcome: 'cleared' },
      { docxId: 7, fit: 'tight', outcome: 'cleared' },
      { docxId: 'p9', fit: 'tight', outcome: 'cleared', extra: '<b>x</b>' },
      'p10',
    ]))).toEqual([{ docxId: 'p9', fit: 'tight', outcome: 'cleared' }]);
  });

  it('is null, not empty, when it could read none of the rows: unreadable is not "no fits"', () => {
    expect(fitOutcomes(review([{ docxId: 'p7', fit: 'tight', outcome: 'not-flagged' }]))).toBeNull();
  });
});
