/**
 * The structure review's confidence -- read from the AST, never invented.
 *
 * The route used to return `confidence: 1.0` for every chapter and
 * `lowConfidenceNodes: []` for every book, whatever the AST said. These pin the
 * two properties that replaced it: a score is reported as the AST carries it,
 * and "not measured" (null) stays distinct from "measured, nothing low" ([]).
 */
import { describe, expect, it } from 'vitest';
import { LOW_CONFIDENCE_BELOW, orphanedOps, structureView } from './manuscripts.js';

const para = (text: string) => ({ type: 'paragraph', content: [{ type: 'text', text }] });
const chapter = (n: number, title: string, confidence?: number) => ({
  type: 'chapter',
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
      { root: 'frontMatter', index: 0, type: 'titlePage', title: null,
        text: 'DEDICATION', confidence: 0.5 },
      { root: 'body', index: 1, type: 'chapter', title: 'NO!',
        text: 'prose of NO!', confidence: 0.7 },
    ]);
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
});

// ── orphanedOps: decisions that would have no effect ──────────────────────

const tagged = (type: string, docxId: string, extra: object = {}) => ({
  type, sourceRef: { docxId }, content: [para(docxId)], ...extra,
});
const op = (id: string, docxId: string) => ({
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
