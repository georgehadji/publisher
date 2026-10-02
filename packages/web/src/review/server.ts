/**
 * Server-side API access for the review pages.
 *
 * Every API route needs a bearer token, and a token in the browser is a token
 * anyone with the page can copy. So pages fetch here, in a Server Component,
 * with the signed-in reviewer's token (./session.ts) -- held in an httpOnly
 * cookie, never in a client bundle -- and hand the browser only the result.
 */

import "server-only";
import type { OverrideOp, StructureReview } from "../types";
import { apiToken } from "./session";

const API_URL = process.env.PUBLISHER_API_URL ?? "http://localhost:4000/v1";

/** The structure review for a manuscript, or `null` if this tenant has no such manuscript. */
export async function fetchStructureReview(
  manuscriptId: string
): Promise<StructureReview | null> {
  const token = await apiToken();
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
  return mutate("PATCH", `/documents/${encodeURIComponent(manuscriptId)}/overrides`, { ops }, ops[0].id);
}

/**
 * Accept one of the model's proposals: the API builds the op from the stored
 * proposal and logs it as `ov-<proposal id>`, which is also the idempotency key.
 */
export async function acceptProposal(
  manuscriptId: string,
  proposalId: string,
  actor: string
): Promise<{ ok: true } | { ok: false; error: string }> {
  return mutate(
    "POST",
    `/manuscripts/${encodeURIComponent(manuscriptId)}/proposals/${encodeURIComponent(proposalId)}/accept`,
    { actor },
    `ov-${proposalId}`
  );
}

async function mutate(
  method: "PATCH" | "POST",
  path: string,
  body: unknown,
  idempotencyKey: string
): Promise<{ ok: true } | { ok: false; error: string }> {
  const token = await apiToken();
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    // Not sent, or sent with no answer -- the log may or may not hold the op.
    return { ok: false, error: "The API did not answer; reload to see whether the op was logged." };
  }
  if (res.ok) return { ok: true };
  const answer = await res.json().catch(() => ({}));
  return { ok: false, error: `API ${res.status}: ${answer.error ?? answer.message ?? res.statusText}` };
}

/** Who a token belongs to, as `GET /v1/whoami` answers; `null` if the API refuses it. */
export async function whoami(
  token: string
): Promise<{ kind: "tenant" | "reviewer"; tenantId: string; reviewer: string | null } | null> {
  const res = await fetch(`${API_URL}/whoami`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}
