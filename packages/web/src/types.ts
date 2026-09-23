/**
 * Publisher Review UI — API contract and component types.
 *
 * From BUILD_PLAN.md §3.17:
 * - Structure review: chapter map, style overrides, front/back matter
 * - Quality review: widow/orphan/runt/river list, each with one-click fixes
 * - Raster view: CDN-served page rasters with proposal-outcome instrumentation
 */

/** A structure review — the first human gate in the workflow. */
export interface StructureReview {
  buildId: string;
  title: string;
  chapters: ChapterReview[];
  /** `null` when the AST carries no scores: not measured, which is not "none low". */
  lowConfidenceNodes: LowConfidenceNode[] | null;
  overrides: OverrideOp[];
}

/** A single chapter in the review. */
export interface ChapterReview {
  number: number;
  title: string;
  id: string;
  pageCount?: number;
  /** As the AST carries it; `null` means unscored, never "certain". */
  confidence: number | null;
  ambiguities: Ambiguity[];
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
  text: string;
  confidence: number;
}

/** An ambiguity flagged for human attention. */
export interface Ambiguity {
  id: string;
  type: string;
  message: string;
  sourceRef: string;
  context: string;
}

/** An override operation — the only way humans (and agents) modify the book. */
export interface OverrideOp {
  id: string;
  sourceRef: string;
  op: "reclassify" | "retitle" | "split" | "merge" | "delete" | "flag_ambiguity";
  from?: string;
  to?: string;
  value?: unknown;
  rationale?: string;
  actor: string;
  createdAt: string;
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
