/**
 * Server-side API access for the review pages.
 *
 * Every API route needs a bearer token, and a token in the browser is a token
 * anyone with the page can copy. So pages fetch here, in a Server Component,
 * with `PUBLISHER_API_TOKEN` -- not `NEXT_PUBLIC_`-prefixed, so Next never
 * inlines it into a client bundle -- and hand the browser only the result.
 */

import "server-only";
import type { StructureReview } from "../types";

const API_URL = process.env.PUBLISHER_API_URL ?? "http://localhost:4000/v1";

/** The structure review for a manuscript, or `null` if this tenant has no such manuscript. */
export async function fetchStructureReview(
  manuscriptId: string
): Promise<StructureReview | null> {
  const token = process.env.PUBLISHER_API_TOKEN;
  if (!token) {
    throw new Error("PUBLISHER_API_TOKEN is not set; the review UI cannot reach the API");
  }
  const res = await fetch(
    `${API_URL}/manuscripts/${encodeURIComponent(manuscriptId)}/structure`,
    { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" }
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}
