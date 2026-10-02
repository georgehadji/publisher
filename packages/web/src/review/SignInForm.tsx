"use client";

import { useActionState } from "react";
import { signIn } from "./actions";

export function SignInForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState(signIn, null);
  return (
    <form action={action} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <input type="hidden" name="next" value={next} />
      <input name="token" type="password" aria-label="Reviewer token" autoComplete="off" required />
      <button type="submit" disabled={pending}>{pending ? "Checking…" : "Sign in"}</button>
      {state && !state.ok && <p role="status" style={{ color: "#c62828" }}>{state.message}</p>}
    </form>
  );
}
