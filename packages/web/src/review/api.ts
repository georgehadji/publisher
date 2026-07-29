/**
 * Review API client.
 *
 * Communicates with the Publisher API for:
 * - Fetching structure review data
 * - Submitting overrides
 * - Querying build status
 */

import type { StructureReview, ReviewSession } from "../types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:4000/v1";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

/** Fetch the structure review for a manuscript. */
export async function fetchStructureReview(
  manuscriptId: string
): Promise<StructureReview> {
  return apiFetch<StructureReview>(`/manuscripts/${manuscriptId}/structure`);
}

/** Submit override operations for a document. */
export async function submitOverrides(
  documentId: string,
  ops: any[]
): Promise<void> {
  await apiFetch(`/documents/${documentId}/overrides`, {
    method: "PATCH",
    body: JSON.stringify({ ops }),
  });
}

/** Get the status of a review session. */
export async function fetchReviewSession(
  buildId: string
): Promise<ReviewSession> {
  return apiFetch<ReviewSession>(`/builds/${buildId}/review`);
}

/** Approve or reject a review. */
export async function updateReviewStatus(
  buildId: string,
  status: "approved" | "rejected"
): Promise<void> {
  await apiFetch(`/builds/${buildId}/review`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}
