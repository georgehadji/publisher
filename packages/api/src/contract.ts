/**
 * The review's wire contract -- what `GET /v1/manuscripts/:id/structure` and
 * `GET /v1/builds/:id` return. The one copy: the routes build their responses
 * against these, and packages/web re-exports them (`export type`, erased at
 * build) instead of keeping its own. No imports on purpose, so both packages'
 * compilers can read it.
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
  /**
   * Which document the chapters describe. `effective`: the latest build's, with
   * the override log as that build applied it -- what was printed. `ingested`:
   * the AST before overrides, when that build stopped before `resolve`. `null`
   * while pending. Ops logged after that build show in `overrides` but not yet here.
   */
  shows: "effective" | "ingested" | null;
  chapters: ChapterReview[];
  /** `null` when the AST carries no scores: not measured, which is not "none low". */
  lowConfidenceNodes: LowConfidenceNode[] | null;
  /** The manuscript's override log, in the order a build applies it. */
  overrides: OverrideOp[];
  /** Logged ops that target no node of this build; `null` when there is no build yet. */
  orphanedOps: OrphanedOp[] | null;
  /**
   * The model's proposals from the same build (`structure-propose`) that no
   * logged op has accepted yet. `null`: no model ran for that build (no API key
   * configured), which is not "no proposals".
   */
  proposals: ProposalReview[] | null;
}

/**
 * The override op each `agent-proposal/1` proposal type becomes when accepted.
 * Pinned to stages/propose_stage.py PROPOSAL_OPS by tests/test_single_source.py.
 */
export const PROPOSAL_OPS = {
  merge_chapters: "merge",
  adjust_heading_level: "demote",
  flag_ambiguity: "flag_ambiguity",
  start_body: "start_body",
  end_body: "end_body",
  reclassify: "reclassify",
  split_chapter: "split",
  promote_heading: "promote",
} as const satisfies Record<string, OverrideOp["op"]>;

/**
 * The parameters each proposal type may carry into its op: accepting copies
 * these, and refuses a proposal carrying any other (B2) or lacking one the op's
 * schema requires; reclassify's `value` (a heading level) is optional (B8).
 * Pinned to stages/propose_stage.py PROPOSAL_PARAMS by tests/test_single_source.py.
 */
export const PROPOSAL_PARAMS = {
  merge_chapters: [],
  adjust_heading_level: [],
  flag_ambiguity: [],
  start_body: [],
  end_body: [],
  reclassify: ["from", "to", "value"],
  split_chapter: [],
  promote_heading: [],
} as const satisfies Record<keyof typeof PROPOSAL_OPS, readonly ("from" | "to" | "value")[]>;

/** One proposal awaiting a reviewer. Accepting it logs op `ov-<id>`. */
export interface ProposalReview {
  id: string;
  type: keyof typeof PROPOSAL_OPS;
  op: OverrideOp["op"];
  docxId: string;
  rationale: string;
  confidence: number | null;
}

/** A single chapter in the review. */
export interface ChapterReview {
  number: number | null;
  title: string;
  /** The title of the part that holds it; `null` for a chapter outside any part. */
  part: string | null;
  /** The `sourceRef.docxId` an override op targets; `null` = untargetable. */
  docxId: string | null;
  /** As the AST carries it; `null` means unscored, never "certain". */
  confidence: number | null;
  /** Ids of the `flag_ambiguity` ops standing on the chapter. */
  flags: string[];
  /** The chapter's own blocks, in order: what a split, promote or insert aims at. */
  blocks: BlockReview[];
}

/** One block directly inside a chapter. */
export interface BlockReview {
  /** The id an op must carry to target it; `null` = untargetable. */
  docxId: string | null;
  type: string;
  /** A heading's level; `null` for anything else. */
  level: number | null;
  /** The block's opening words, at most BLOCK_EXCERPT_CHARS. */
  excerpt: string;
  /** Ids of the `flag_ambiguity` ops standing on the block. */
  flags: string[];
}

/** How much of a block's text the review shows. */
export const BLOCK_EXCERPT_CHARS = 80;

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
    | "retitle" | "set_attr" | "flag_ambiguity" | "resolve_ambiguity" | "start_body"
    | "end_body";
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

/**
 * One diagnostic a stage reported: a warning from a stage that completed, or
 * the diagnostics of one that failed. `Diagnostic.to_dict()` in
 * platform/stages/py/publisher_stages writes exactly this shape.
 */
export interface StageDiagnostic {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  suggestedFix: string | null;
  sourceRef: string | null;
}

/** One stage of a build, as `GET /v1/builds/:id` returns it. */
export interface BuildStage {
  name: string;
  status: "running" | "completed" | "failed";
  durationMs: number;
  cacheHit: boolean;
  /** As the stage reported them; `{}` when it reported none. */
  metrics: Record<string, unknown>;
  /** `null` for a row recorded before diagnostics were kept: unknown, not "none". */
  diagnostics: StageDiagnostic[] | null;
}
