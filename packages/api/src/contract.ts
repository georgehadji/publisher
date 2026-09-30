/**
 * The structure review's wire contract -- what `GET /v1/manuscripts/:id/structure`
 * returns. The one copy: routes/manuscripts.ts builds its response against these,
 * and packages/web re-exports them (`export type`, erased at build) instead of
 * keeping its own. No imports on purpose, so both packages' compilers can read it.
 */

/**
 * Below this, a structural decision goes to review. The same line
 * `publisher_structure.rules.ESCALATE_BELOW` sets (LLM_STRATEGY.md §5); pinned
 * to it, and to the review UI's copy, by tests/test_single_source.py.
 */
export const LOW_CONFIDENCE_BELOW = 0.8;

export interface StructureReview {
  manuscriptId: string;
  /** `pending`: no build has produced an AST yet, so there is nothing to review. */
  status: "pending" | "ready";
  chapters: ChapterReview[];
  /** `null` when the AST carries no scores: not measured, which is not "none low". */
  lowConfidenceNodes: LowConfidenceNode[] | null;
  /** The manuscript's override log, in the order a build applies it. */
  overrides: OverrideOp[];
  /** Logged ops that target no node of this build; `null` when there is no build yet. */
  orphanedOps: OrphanedOp[] | null;
}

/** A single chapter in the review. */
export interface ChapterReview {
  number: number | null;
  title: string;
  /** The `sourceRef.docxId` an override op targets; `null` = untargetable. */
  docxId: string | null;
  /** As the AST carries it; `null` means unscored, never "certain". */
  confidence: number | null;
}

/**
 * A section whose structural type ingest was unsure of (below
 * LOW_CONFIDENCE_BELOW) -- a chapter, or a front/back-matter section.
 */
export interface LowConfidenceNode {
  root: "frontMatter" | "body" | "backMatter";
  index: number;
  type: string;
  title: string | null;
  /** `null` for a front/back-matter section wrapper: the schema gives it no sourceRef. */
  docxId: string | null;
  text: string;
  confidence: number;
}

/**
 * An override operation — the only way humans (and agents) modify the book.
 * `overrides/1`'s `$defs.overrideOp`; the API refuses anything else with a 400.
 */
export interface OverrideOp {
  id: string;
  sourceRef: { docxId: string; contentHash?: string; fallbackText?: string };
  op:
    | "reclassify" | "split" | "merge" | "promote" | "demote" | "delete" | "insert"
    | "retitle" | "rename" | "set_attr" | "flag_ambiguity" | "resolve_ambiguity";
  path?: string;
  from?: string;
  to?: string;
  value?: unknown;
  rationale?: string;
  actor: string;
  at: string;
}

/** A logged op that matches no node, so a build skips it. */
export interface OrphanedOp {
  op: OverrideOp;
  reason: "no_source_ref";
}
