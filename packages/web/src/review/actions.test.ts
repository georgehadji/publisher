/**
 * The review UI's Server Actions are public POST endpoints: every refusal path
 * is exercised here, and every op a form can build is validated against
 * `overrides/1` itself -- the schema the API enforces -- so a form that drifts
 * from it fails here rather than as a 400 in front of a reviewer.
 */
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Ajv2020 from "ajv/dist/2020";

const cookieJar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (cookieJar.has(name) ? { value: cookieJar.get(name) } : undefined) }),
}));
vi.mock("next/navigation", () => ({
  redirect: vi.fn((to: string) => { throw new Error(`redirect:${to}`); }),
}));
vi.mock("next/cache", () => ({ refresh: vi.fn() }));
vi.mock("./server", () => ({
  appendOverrides: vi.fn(async () => ({ ok: true })),
  acceptProposal: vi.fn(async () => ({ ok: true })),
  whoami: vi.fn(async (token: string) =>
    token === "reviewer-token" ? { kind: "reviewer", reviewer: "ada" }
      : token === "tenant-token" ? { kind: "tenant" } : null),
}));
vi.mock("./session", () => ({
  SESSION_COOKIE: "publisher_session",
  localDevToken: () => (process.env.PUBLISHER_REVIEW_LOCAL_DEV === "1" ? "dev" : null),
  startSession: vi.fn(async () => undefined),
  endSession: vi.fn(async () => undefined),
  safeNext: (next: unknown) => (typeof next === "string" && /^\/(?![/\\])/.test(next) ? next : "/"),
}));

import { acceptProposalAction, signIn, submitOverride } from "./actions";
import { acceptProposal, appendOverrides } from "./server";
import { startSession } from "./session";

const schema = JSON.parse(readFileSync(new URL("../../../../schemas/overrides/overrides.schema.json", import.meta.url), "utf8"));
// `format` is left unchecked here (no ajv-formats); `at` is asserted separately.
const validateSet = new Ajv2020({ strict: false, validateFormats: false }).compile(schema);

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [k, v] of Object.entries(fields)) data.set(k, v);
  return data;
}

function lastOp() {
  const calls = vi.mocked(appendOverrides).mock.calls;
  return calls[calls.length - 1][1][0];
}

beforeEach(() => {
  vi.clearAllMocks();
  cookieJar.clear();
  cookieJar.set("publisher_session", "reviewer-token");
  delete process.env.PUBLISHER_REVIEW_LOCAL_DEV;
});

describe("submitOverride refuses", () => {
  it.each([
    ["an empty manuscript id", "", "p1", { op: "retitle", text: "T" }, "Invalid target."],
    ["an empty target id", "m1", "", { op: "retitle", text: "T" }, "Invalid target."],
    ["an over-long target id", "m1", "x".repeat(129), { op: "retitle", text: "T" }, "Invalid target."],
    ["an op the form does not offer", "m1", "p1", { op: "set_attr" }, "Unsupported operation."],
    ["a retype to no section type", "m1", "f1", { op: "reclassify", from: "preface", to: "paragraph" }, "Pick a different section type."],
    ["a retype from no section type", "m1", "f1", { op: "reclassify", from: "chapter", to: "preface" }, "Pick a different section type."],
    ["a retype to itself", "m1", "f1", { op: "reclassify", from: "preface", to: "preface" }, "Pick a different section type."],
    ["a prototype key as an op", "m1", "p1", { op: "constructor" }, "Unsupported operation."],
    ["deleting author text", "m1", "p1", { op: "delete" }, "Only an inserted break can be deleted here."],
    ["a malformed flag", "m1", "p1", { op: "resolve_ambiguity", flag: "not-a-flag" }, "Invalid flag."],
    ["a retitle with no text", "m1", "p1", { op: "retitle", text: "   " }, "Enter 1–256 characters."],
    ["a retitle over its limit", "m1", "p1", { op: "retitle", text: "x".repeat(257) }, "Enter at most 256 characters."],
    ["text on an op that takes none", "m1", "p1", { op: "split", text: "why" }, "This operation takes no text."],
  ])("%s", async (_name, manuscriptId, docxId, fields, message) => {
    const result = await submitOverride(manuscriptId, docxId, null, form(fields));
    expect(result).toEqual({ ok: false, message });
    expect(appendOverrides).not.toHaveBeenCalled();
  });

  it("anyone not signed in", async () => {
    cookieJar.clear();
    const result = await submitOverride("m1", "p1", null, form({ op: "split" }));
    expect(result).toEqual({ ok: false, message: "Sign in to log a decision." });
  });

  it("a session whose token is not a reviewer's", async () => {
    cookieJar.set("publisher_session", "tenant-token");
    const result = await submitOverride("m1", "p1", null, form({ op: "split" }));
    expect(result?.ok).toBe(false);
    expect(appendOverrides).not.toHaveBeenCalled();
  });

  it("passes on the API's refusal", async () => {
    vi.mocked(appendOverrides).mockResolvedValueOnce({ ok: false, error: "conflict" } as never);
    const result = await submitOverride("m1", "p1", null, form({ op: "split" }));
    expect(result).toEqual({ ok: false, message: "conflict" });
  });
});

describe("every op a form builds is a valid overrides/1 op", () => {
  it.each([
    ["retitle", "p1", { op: "retitle", text: "A New Title" }],
    ["flag_ambiguity", "p1", { op: "flag_ambiguity", text: "Is this a chapter?" }],
    ["resolve_ambiguity", "p1", { op: "resolve_ambiguity", flag: "ov-1234" }],
    ["merge", "p1", { op: "merge" }],
    ["merge with a reason", "p1", { op: "merge", text: "Same scene." }],
    ["split", "p1", { op: "split" }],
    ["promote", "p1", { op: "promote" }],
    ["demote", "p1", { op: "demote" }],
    ["insert", "p1", { op: "insert" }],
    ["delete of an inserted break", "ins-0123456789ab", { op: "delete" }],
    ["start_body", "f1", { op: "start_body" }],
    ["end_body", "b1", { op: "end_body" }],
    ["a section retype", "f1", { op: "reclassify", from: "preface", to: "foreword" }],
  ])("%s", async (_name, docxId, fields) => {
    const result = await submitOverride("m1", docxId, null, form(fields));
    expect(result?.ok).toBe(true);
    const op = lastOp();
    const valid = validateSet({ schema: "overrides/1", documentId: "m1", astVersion: 1, ops: [op] });
    expect(validateSet.errors ?? []).toEqual([]);
    expect(valid).toBe(true);
    expect(op.actor).toBe("user:ada");
    expect(op.sourceRef).toEqual({ docxId });
    expect(new Date(op.at).toISOString()).toBe(op.at);
  });

  it("the validator can fail: an insert with no value is refused", () => {
    const op = { id: "ov-x", sourceRef: { docxId: "p1" }, op: "insert", actor: "user:ada", at: "2026-01-01T00:00:00.000Z" };
    expect(validateSet({ schema: "overrides/1", documentId: "m1", astVersion: 1, ops: [op] })).toBe(false);
  });

  it("writes as the local reviewer only under PUBLISHER_REVIEW_LOCAL_DEV=1", async () => {
    cookieJar.clear();
    process.env.PUBLISHER_REVIEW_LOCAL_DEV = "1";
    await submitOverride("m1", "p1", null, form({ op: "split" }));
    expect(lastOp().actor).toBe("user:local-reviewer");
  });
});

describe("acceptProposalAction", () => {
  it("refuses a malformed proposal id", async () => {
    expect(await acceptProposalAction("m1", "ov-1", null, form({})))
      .toEqual({ ok: false, message: "Invalid proposal." });
    expect(acceptProposal).not.toHaveBeenCalled();
  });

  it("refuses anyone not signed in", async () => {
    cookieJar.clear();
    expect(await acceptProposalAction("m1", "pr-1", null, form({})))
      .toEqual({ ok: false, message: "Sign in to accept a proposal." });
  });

  it("accepts as the signed-in reviewer", async () => {
    expect((await acceptProposalAction("m1", "pr-1", null, form({})))?.ok).toBe(true);
    expect(acceptProposal).toHaveBeenCalledWith("m1", "pr-1", "user:ada");
  });
});

describe("signIn", () => {
  it.each([["no token", ""], ["an over-long token", "x".repeat(513)]])("refuses %s", async (_n, token) => {
    expect(await signIn(null, form({ token }))).toEqual({ ok: false, message: "Enter your reviewer token." });
  });

  it("refuses a tenant token: a person must not write as whatever actor it names", async () => {
    expect(await signIn(null, form({ token: "tenant-token" })))
      .toEqual({ ok: false, message: "That is not a reviewer token." });
    expect(startSession).not.toHaveBeenCalled();
  });

  it("opens a session and redirects only to a path on this site", async () => {
    await expect(signIn(null, form({ token: "reviewer-token", next: "//evil.example" })))
      .rejects.toThrow(/^redirect:\/$/);
    expect(startSession).toHaveBeenCalledWith("reviewer-token");
  });
});
