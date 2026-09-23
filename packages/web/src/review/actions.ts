"use server";

/**
 * The review UI's one write: append a reviewer's decision to the override log.
 *
 * NO AUTHENTICATION. A Server Action is a public POST endpoint, and this one
 * writes to an append-only log with no undo using the server's tenant token.
 * The review UI is local-dev only: `npm run dev`/`start` bind 127.0.0.1, and it
 * must never be exposed beyond this machine until it authenticates reviewers.
 *
 * The client names a target and supplies text; the op itself -- id, actor,
 * timestamp, shape -- is built here, so the browser can't assert any of it.
 */

import { randomUUID } from "node:crypto";
import { refresh } from "next/cache";
import type { OverrideOp } from "../types";
import { appendOverrides } from "./server";

// ponytail: no reviewer identity exists without auth, so every op is
// attributed to this one actor. Per-reviewer actors need sign-in first.
const ACTOR = "user:local-reviewer";

/** The ops this form can write. `delete`/`reclassify` stay out: irreversible, or need a type picker. */
const FORM_OPS = { retitle: 256, flag_ambiguity: 4096 } as const;

export type SubmitState = { ok: boolean; message: string } | null;

export async function submitOverride(
  manuscriptId: string,
  docxId: string,
  _prev: SubmitState,
  formData: FormData
): Promise<SubmitState> {
  // Every argument is client-controlled, bound ones included.
  const op = formData.get("op");
  const text = String(formData.get("text") ?? "").trim();
  if (typeof manuscriptId !== "string" || !manuscriptId
      || typeof docxId !== "string" || !docxId || docxId.length > 128) {
    return { ok: false, message: "Invalid target." };
  }
  if (op !== "retitle" && op !== "flag_ambiguity") {
    return { ok: false, message: "Unsupported operation." };
  }
  if (!text || text.length > FORM_OPS[op]) {
    return { ok: false, message: `Enter 1–${FORM_OPS[op]} characters.` };
  }

  const draft: OverrideOp = {
    id: `ov-${randomUUID()}`,
    sourceRef: { docxId },
    op,
    ...(op === "retitle" ? { value: text } : { rationale: text }),
    actor: ACTOR,
    at: new Date().toISOString(),
  };
  const result = await appendOverrides(manuscriptId, [draft]);
  if (!result.ok) return { ok: false, message: result.error };
  refresh();
  return { ok: true, message: "Logged. It takes effect on the next build." };
}
