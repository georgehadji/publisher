import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE } from "./review/session-cookie";

/**
 * Sends a visitor with no reviewer session to the sign-in page (W2). An
 * optimistic check only, as Next's own guide puts it: the API decides, on
 * every call, whether the token in the cookie is a reviewer's. Local
 * development (`PUBLISHER_REVIEW_LOCAL_DEV=1`) needs no session.
 */
export function proxy(request: NextRequest) {
  if (process.env.PUBLISHER_REVIEW_LOCAL_DEV === "1") return NextResponse.next();
  if (request.cookies.get(SESSION_COOKIE)?.value) return NextResponse.next();
  const url = new URL("/sign-in", request.url);
  url.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: "/manuscripts/:path*",
};
