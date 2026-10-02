/** The reviewer session cookie's name: read by ./session.ts and by src/proxy.ts,
 * which cannot import a `server-only` module that reads `next/headers`. */
export const SESSION_COOKIE = "publisher_reviewer";
