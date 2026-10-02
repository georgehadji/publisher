"use server";

/**
 * The review UI's writes: append a reviewer's decision to the override log, and
 * sign a reviewer in or out.
 *
 * A Server Action is a public POST endpoint, so every one here acts only with
 * the caller's own credentials: the signed-in reviewer's token (./session.ts),
 * whose ops the API attributes to them and no one else. Without a session the
 * write is refused -- except in local development (`PUBLISHER_REVIEW_LOCAL_DEV=1`),
 * which writes as the one actor `user:local-reviewer`.
 *
 * The client names a target and supplies text; the op itself -- id, actor,
 * timestamp, shape -- is built here, so the browser can't assert any of it.
 */

import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { refresh } from "next/cache";
import type { Route } from "next";
import type { OverrideOp } from "../types";
import { acceptProposal, appendOverrides, whoami } from "./server";
import { SESSION_COOKIE, endSession, localDevToken, safeNext, startSession } from "./session";

const LOCAL_DEV_ACTOR = "user:local-reviewer";

/** The actor the caller's ops carry, or `null` when nobody is signed in. */
async function currentActor(): Promise<string | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (token) {
    const me = await whoami(token);
    return me?.kind === "reviewer" && me.reviewer ? `user:${me.reviewer}` : null;
  }
  return localDevToken() ? LOCAL_DEV_ACTOR : null;
}

type Text = "required" | "optional" | "none";

/**
 * The ops this UI can write: whether each takes the form's text, its limit, and
 * the fields the op is built with. `reclassify` stays out (it needs a type
 * picker), and so does `delete` of author text: the log has no undo. `delete`
 * appears only for a break the reviewer inserted (`ins-` ids), which is how an
 * insert is taken back. `merge`/`demote` need no text; any given is the rationale.
 */
const FORM_OPS: Record<string, { text: Text; max: number; fields: (text: string, flag: string) => Partial<OverrideOp> }> = {
  retitle: { text: "required", max: 256, fields: (text) => ({ value: text }) },
  flag_ambiguity: { text: "required", max: 4096, fields: (text) => ({ rationale: text }) },
  resolve_ambiguity: { text: "none", max: 0, fields: (_t, flag) => (flag ? { value: flag } : {}) },
  merge: { text: "optional", max: 4096, fields: (text) => (text ? { rationale: text } : {}) },
  split: { text: "none", max: 0, fields: () => ({}) },
  promote: { text: "none", max: 0, fields: () => ({}) },
  demote: { text: "optional", max: 4096, fields: (text) => (text ? { rationale: text } : {}) },
  insert: { text: "none", max: 0, fields: () => ({ value: "sceneBreak" }) },
  delete: { text: "none", max: 0, fields: () => ({}) },
};

const INSERTED = /^ins-[0-9a-f]{12}$/;
const OP_ID = /^ov-[a-zA-Z0-9_-]{1,61}$/;

export type SubmitState = { ok: boolean; message: string } | null;

export async function submitOverride(
  manuscriptId: string,
  docxId: string,
  _prev: SubmitState,
  formData: FormData
): Promise<SubmitState> {
  // Every argument is client-controlled, bound ones included.
  const op = String(formData.get("op") ?? "");
  const text = String(formData.get("text") ?? "").trim();
  const flag = String(formData.get("flag") ?? "");
  if (typeof manuscriptId !== "string" || !manuscriptId
      || typeof docxId !== "string" || !docxId || docxId.length > 128) {
    return { ok: false, message: "Invalid target." };
  }
  const spec = Object.hasOwn(FORM_OPS, op) ? FORM_OPS[op] : undefined;
  if (!spec) {
    return { ok: false, message: "Unsupported operation." };
  }
  if (op === "delete" && !INSERTED.test(docxId)) {
    return { ok: false, message: "Only an inserted break can be deleted here." };
  }
  if (flag && !OP_ID.test(flag)) {
    return { ok: false, message: "Invalid flag." };
  }
  if (spec.text === "required" && !text) {
    return { ok: false, message: `Enter 1–${spec.max} characters.` };
  }
  if (spec.text === "none" ? text.length > 0 : text.length > spec.max) {
    return { ok: false, message: spec.text === "none" ? "This operation takes no text." : `Enter at most ${spec.max} characters.` };
  }

  const actor = await currentActor();
  if (!actor) {
    return { ok: false, message: "Sign in to log a decision." };
  }
  const draft = {
    id: `ov-${randomUUID()}`,
    sourceRef: { docxId },
    op,
    ...spec.fields(text, flag),
    actor,
    at: new Date().toISOString(),
  } as OverrideOp;
  const result = await appendOverrides(manuscriptId, [draft]);
  if (!result.ok) return { ok: false, message: result.error };
  refresh();
  return { ok: true, message: "Logged. It takes effect on the next build." };
}

const PROPOSAL_ID = /^pr-[a-zA-Z0-9_-]{1,61}$/;

/**
 * Accepts a model proposal. The caller names which one; what it does is the
 * stored proposal's, built into an op by the API (D9: this click is the human
 * acceptance a model's output needs before any build applies it).
 */
export async function acceptProposalAction(
  manuscriptId: string,
  proposalId: string,
  _prev: SubmitState,
  _formData: FormData
): Promise<SubmitState> {
  if (typeof manuscriptId !== "string" || !manuscriptId
      || typeof proposalId !== "string" || !PROPOSAL_ID.test(proposalId)) {
    return { ok: false, message: "Invalid proposal." };
  }
  const actor = await currentActor();
  if (!actor) {
    return { ok: false, message: "Sign in to accept a proposal." };
  }
  const result = await acceptProposal(manuscriptId, proposalId, actor);
  if (!result.ok) return { ok: false, message: result.error };
  refresh();
  return { ok: true, message: "Accepted. It takes effect on the next build." };
}

/**
 * Signs a reviewer in with their token. Only a reviewer token opens a browser
 * session: a tenant (service) token writes as whatever actor it names, which
 * is right for an agent and wrong for a person.
 */
export async function signIn(_prev: SubmitState, formData: FormData): Promise<SubmitState> {
  const token = String(formData.get("token") ?? "").trim();
  if (!token || token.length > 512) {
    return { ok: false, message: "Enter your reviewer token." };
  }
  const me = await whoami(token);
  if (me?.kind !== "reviewer") {
    return { ok: false, message: "That is not a reviewer token." };
  }
  await startSession(token);
  // A path on this site (safeNext), but not one typed routes can check statically.
  redirect(safeNext(formData.get("next")) as Route);
}

export async function signOut(): Promise<void> {
  await endSession();
  redirect("/sign-in");
}
