/**
 * Who is reviewing (W2, docs/WIRING_PLAN.md).
 *
 * A reviewer signs in with their own token (the API's PUBLISHER_REVIEWER_TOKENS),
 * kept in an httpOnly cookie the browser's scripts cannot read. Every API call
 * the review pages and actions make carries THAT token, so the API knows the
 * reviewer and attributes their ops to them -- it refuses an op attributed to
 * anyone else.
 *
 * Local development may instead use the shared `PUBLISHER_API_TOKEN`, but only
 * when `PUBLISHER_REVIEW_LOCAL_DEV=1` says so: a deployment that sets the token
 * for some other reason does not quietly fall back to one shared identity.
 */

import "server-only";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "./session-cookie";

export { SESSION_COOKIE };
/** How long a sign-in lasts: one working day. */
export const SESSION_MAX_AGE_S = 8 * 60 * 60;

/** Thrown when a page or action needs an API token and has none. */
export class SignInRequired extends Error {
  constructor() {
    super("Sign in to review.");
  }
}

export function localDevToken(): string | null {
  return process.env.PUBLISHER_REVIEW_LOCAL_DEV === "1"
    ? process.env.PUBLISHER_API_TOKEN || null
    : null;
}

/** The token to call the API with: the signed-in reviewer's, else local dev's. */
export async function apiToken(): Promise<string> {
  const reviewer = (await cookies()).get(SESSION_COOKIE)?.value;
  const token = reviewer || localDevToken();
  if (!token) throw new SignInRequired();
  return token;
}

export async function startSession(token: string): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    // Browsers keep a Secure cookie on http://localhost; anything else is https.
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: SESSION_MAX_AGE_S,
  });
}

export async function endSession(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}

/** A sign-in's `next` target, only when it is a path on this site. */
export function safeNext(next: unknown): string {
  return typeof next === "string" && /^\/(?![/\\])/.test(next) ? next : "/";
}
