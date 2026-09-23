/**
 * Publisher Review UI — API contract and component types.
 *
 * From BUILD_PLAN.md §3.17:
 * - Structure review: chapter map, style overrides, front/back matter
 * - Quality review: widow/orphan/runt/river list, each with one-click fixes
 * - Raster view: CDN-served page rasters with proposal-outcome instrumentation
 */

/**
 * A structure review — the first human gate. Exactly what the API's
 * `GET /v1/manuscripts/:id/structure` returns (packages/api routes/manuscripts.ts).
 */
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
 * A section whose structural type ingest was unsure of (below 0.8) -- a
 * chapter, or a front/back-matter section. Mirrors the API's `structureView`.
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

/** A quality review item. */
export interface QualityIssue {
  type: "widow" | "orphan" | "runt" | "river" | "hyphen-stack" | "short-chapter-end";
  pageNumber: number;
  severity: "error" | "warning" | "info";
  description: string;
  sourceRef: string;
  fix: string;
  beforeCrop: string; // raster image URL
  afterCrop?: string; // raster image URL with fix applied
}

/** Proposal from the Structure Wrangler agent. */
export interface AgentProposal {
  id: string;
  type: string;
  sourceRef: string;
  rationale: string;
  confidence: number;
  accepted: boolean;
  actor?: string;
}

/** Review session state. */
export interface ReviewSession {
  buildId: string;
  status: "pending" | "in_review" | "approved" | "rejected";
  overridesApplied: number;
  ambiguitiesResolved: number;
  lowConfidenceResolved: number;
  startedAt: string;
  completedAt?: string;
}
