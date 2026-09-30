/**
 * Publisher Review UI — API contract and component types.
 *
 * From BUILD_PLAN.md §3.17:
 * - Structure review: chapter map, style overrides, front/back matter
 * - Quality review: widow/orphan/runt/river list, each with one-click fixes
 * - Raster view: CDN-served page rasters with proposal-outcome instrumentation
 */

/**
 * The structure review's contract -- `GET /v1/manuscripts/:id/structure`. The API
 * owns it (packages/api/src/contract.ts); a type-only re-export, so nothing of the
 * API reaches this bundle and there is no second copy here to drift.
 */
export type {
  StructureReview, ChapterReview, LowConfidenceNode, OverrideOp, OrphanedOp,
} from "../../api/src/contract";

/**
 * Below this, a structural decision goes to review. A value, so it cannot be
 * re-exported type-only; pinned to the API's and to
 * publisher_structure.rules.ESCALATE_BELOW by tests/test_single_source.py.
 */
export const LOW_CONFIDENCE_BELOW = 0.8;

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
