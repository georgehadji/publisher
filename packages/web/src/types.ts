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
  lowConfidenceNodes: LowConfidenceNode[];
  overrides: OverrideOp[];
}

/** A single chapter in the review. */
export interface ChapterReview {
  number: number;
  title: string;
  id: string;
  pageCount?: number;
  confidence: number;
  ambiguities: Ambiguity[];
}

/** A node the rules engine marked as low-confidence. */
export interface LowConfidenceNode {
  blockIndex: number;
  text: string;
  suggested: string;
  confidence: number;
  alternatives: string[];
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
