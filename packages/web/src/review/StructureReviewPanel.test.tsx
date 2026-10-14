/**
 * What the first human gate shows, rendered on the server as React would: the
 * states that must not read as "all clear" (pending, unscored, no model run)
 * and the controls offered on each kind of node.
 */
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { OverrideOp, StructureReview } from "../types";

vi.mock("./actions", () => ({
  submitOverride: vi.fn(),
  acceptProposalAction: vi.fn(),
  signOut: vi.fn(),
}));

import { StructureReviewPanel } from "./StructureReviewPanel";

function review(over: Partial<StructureReview> = {}): StructureReview {
  return {
    manuscriptId: "m1",
    status: "ready",
    shows: "effective",
    chapters: [
      { number: 1, title: "Arrival", part: null, docxId: "p1", confidence: 0.95, flags: [], blocks: [] },
      { number: null, title: "Untitled", part: null, docxId: null, confidence: null, flags: [], blocks: [] },
    ],
    lowConfidenceNodes: [],
    overrides: [],
    orphanedOps: [],
    proposals: [],
    fitOutcomes: null,
    ...over,
  };
}

const render = (r: StructureReview) => renderToStaticMarkup(<StructureReviewPanel review={r} />);

describe("StructureReviewPanel", () => {
  it("says there is nothing to review before any build", () => {
    expect(render(review({ status: "pending" }))).toContain("nothing to review");
  });

  it("marks an unscored chapter as unscored, not certain", () => {
    const html = render(review());
    expect(html).toContain("Ch. 1: Arrival");
    expect(html).toContain("95%");
    expect(html).toContain("unscored");
  });

  it("says which document it shows", () => {
    expect(render(review({ shows: "ingested" }))).toContain("before overrides");
  });

  it("does not present unscored structure as checked", () => {
    expect(render(review({ lowConfidenceNodes: null }))).toContain("was not scored");
  });

  it("lists low-confidence nodes with their target ids", () => {
    const html = render(review({ lowConfidenceNodes: [
      { root: "body", index: 0, type: "chapter", title: "Prologue?", docxId: "p9", text: "", confidence: 0.4 },
    ] }));
    expect(html).toContain("Low-Confidence Nodes (1)");
    expect(html).toContain("40%");
    expect(html).toContain("p9");
  });

  it("offers each model proposal as advice with an Accept button", () => {
    const html = render(review({ proposals: [
      { id: "pr-1", type: "merge_chapters", op: "merge", docxId: "p1", rationale: "Same scene.", confidence: 0.7, page: null, kind: null },
    ] }));
    expect(html).toContain("Model proposals (1)");
    expect(html).toContain("Same scene.");
    expect(html).toContain(">Accept<");
    expect(html).not.toContain("Page ");
  });

  it("shows a layout proposal's page and defect (K4)", () => {
    const html = render(review({ proposals: [
      { id: "pr-2", type: "fit_paragraph", op: "set_attr", docxId: "p7",
        rationale: "A widow on page 5: set its paragraph's fit to tight to pull the line back.",
        confidence: null, page: 5, kind: "widow" },
    ] }));
    expect(html).toContain("Page 5 · widow");
    expect(html).toContain("set_attr");
    expect(html).toContain(">Accept<");
  });

  it("says what each applied fit did in this build, in words (K5)", () => {
    const html = render(review({ fitOutcomes: [
      { docxId: "p7", fit: "tight", outcome: "cleared" },
      { docxId: "p8", fit: "tighter", outcome: "still-defective" },
    ] }));
    expect(html).toContain("Fit outcomes (2)");
    expect(html).toContain("p7");
    expect(html).toContain("Cleared");
    expect(html).toContain("Still a widow or runt");
  });

  it("does not present an unmeasured fit as cleared", () => {
    const html = render(review({ fitOutcomes: [{ docxId: "p9", fit: "tight", outcome: "unmeasured" }] }));
    expect(html).toContain("Not measured");
    expect(html).not.toContain("Cleared");
  });

  it("says a logged fit was not checked when this build ran no layout review", () => {
    const fit: OverrideOp = { id: "ov-pr-1", sourceRef: { docxId: "p7" }, op: "set_attr", path: "/attrs/fit",
      value: "tight", actor: "user:ada", at: "2026-10-14T00:00:00.000Z" };
    expect(render(review({ overrides: [fit] }))).toContain("not checked");
    expect(render(review())).not.toContain("Fit outcomes");
    expect(render(review({ fitOutcomes: [] }))).not.toContain("Fit outcomes");
  });

  it("says a fit accepted since this build has no outcome until the next one", () => {
    const fit = (id: string, docxId: string): OverrideOp => ({ id, sourceRef: { docxId }, op: "set_attr",
      path: "/attrs/fit", value: "tight", actor: "user:ada", at: "2026-10-14T00:00:00.000Z" });
    const measured = [{ docxId: "p7", fit: "tight" as const, outcome: "cleared" as const }];
    expect(render(review({ fitOutcomes: [], overrides: [fit("ov-1", "p8")] })))
      .toContain("1 fit in the override log has no outcome yet");
    expect(render(review({ fitOutcomes: measured, overrides: [fit("ov-1", "p7"), fit("ov-2", "p8")] })))
      .toContain("1 fit in the override log has no outcome yet");
    expect(render(review({ fitOutcomes: measured, overrides: [fit("ov-1", "p7")] })))
      .not.toContain("no outcome yet");
  });

  it("lists logged ops that reach nothing in this build", () => {
    const html = render(review({ orphanedOps: [{ reason: "no_source_ref", op: {
      id: "ov-1", sourceRef: { docxId: "gone" }, op: "retitle", value: "Old", actor: "user:ada", at: "2026-01-01T00:00:00.000Z",
    } }] }));
    expect(html).toContain("Overrides with no effect (1)");
    expect(html).toContain("gone");
    expect(html).toContain("user:ada");
  });

  it("shows no chapter detail until one is chosen", () => {
    expect(render(review())).toContain("Select a chapter");
  });

  it("offers a front section the body's start and a retype to its own end's types (B9)", () => {
    const html = render(review({ lowConfidenceNodes: [
      { root: "frontMatter", index: 0, type: "preface", title: null, docxId: "f1", text: "THE STORM", confidence: 0.5 },
      { root: "backMatter", index: 0, type: "epilogue", title: null, docxId: "e1", text: "AFTER", confidence: 0.5 },
    ] }));
    expect(html).toContain("The body starts here");
    expect(html).toContain("The body ends here");
    expect(html).toContain('<option value="foreword">');
    expect(html).not.toContain('<option value="preface">');        // not its own type
    expect(html).toContain('<option value="colophon">');          // back types for the back row
  });
});
