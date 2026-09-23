/**
 * Server-side API access for the review pages.
 *
 * Every API route needs a bearer token, and a token in the browser is a token
 * anyone with the page can copy. So pages fetch here, in a Server Component,
 * with `PUBLISHER_API_TOKEN` -- not `NEXT_PUBLIC_`-prefixed, so Next never
 * inlines it into a client bundle -- and hand the browser only the result.
 */

import "server-only";
import type { OverrideOp, StructureReview } from "../types";

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

/**
 * Append ops to a manuscript's override log. The API requires an
 * `Idempotency-Key` on every mutation; the first op's id serves, since op ids
 * are fresh per submission and the API scopes keys per tenant. A resent request
 * replays the stored response rather than appending twice.
 */
export async function appendOverrides(
  manuscriptId: string,
  ops: OverrideOp[]
): Promise<{ ok: true } | { ok: false; error: string }> {
  const token = process.env.PUBLISHER_API_TOKEN;
  if (!token) {
    throw new Error("PUBLISHER_API_TOKEN is not set; the review UI cannot reach the API");
  }
  let res: Response;
  try {
    res = await fetch(
      `${API_URL}/documents/${encodeURIComponent(manuscriptId)}/overrides`,
      {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "Idempotency-Key": ops[0].id,
        },
        body: JSON.stringify({ ops }),
        cache: "no-store",
      }
    );
  } catch {
    // Not sent, or sent with no answer -- the log may or may not hold the op.
    return { ok: false, error: "The API did not answer; reload to see whether the op was logged." };
  }
  if (res.ok) return { ok: true };
  const body = await res.json().catch(() => ({}));
  return { ok: false, error: `API ${res.status}: ${body.error ?? body.message ?? res.statusText}` };
}
