import { SignInForm } from "../../review/SignInForm";

/** Reviewer sign-in: a reviewer's own token, checked against the API. */
export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  return (
    <main style={{ maxWidth: 420, margin: "80px auto", fontFamily: "-apple-system, sans-serif" }}>
      <h1 style={{ fontSize: 20 }}>Sign in to review</h1>
      <p style={{ color: "#555", fontSize: 14 }}>
        Use the reviewer token you were given. Your decisions are logged under your name.
      </p>
      <SignInForm next={typeof next === "string" ? next : "/"} />
    </main>
  );
}
