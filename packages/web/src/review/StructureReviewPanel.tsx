"use client";

import React, { useActionState, useState } from "react";
import { submitOverride } from "./actions";
import type {
  StructureReview,
  ChapterReview,
  LowConfidenceNode,
  OrphanedOp,
  OverrideOp,
} from "../types";

/**
 * StructureReviewPanel — the first human gate.
 *
 * Displays:
 * - Chapter map with confidence indicators
 * - Low-confidence nodes requiring classification
 * - Each node's override target id, and the logged ops aimed at it
 * - Logged ops that target nothing in this build
 *
 * A chapter with a target id can be retitled or flagged; the op is appended to
 * the log and takes effect on the next build. Local-dev only -- see ./actions.ts.
 *
 * From ARCHITECTURE.md §1.1 (Human gate #1):
 * "chapter map, front/back matter, flagged ambiguities"
 */
export function StructureReviewPanel({ review }: { review: StructureReview }) {
  const [active, setActive] = useState<number | null>(null);

  if (review.status === "pending") {
    return (
      <p style={styles.placeholder}>
        No build has produced this manuscript's structure yet, so there is nothing to review.
      </p>
    );
  }

  return (
    <div className="structure-review" style={styles.container}>
      <div style={styles.sidebar}>
        <h2 style={styles.title}>Chapters</h2>
        <div style={styles.chapterList}>
          {review.chapters.map((ch, i) => (
            <ChapterCard
              key={ch.docxId ?? `index-${i}`}
              chapter={ch}
              active={active === i}
              onClick={() => setActive(i)}
            />
          ))}
        </div>
      </div>
      <div style={styles.main}>
        {active !== null && review.chapters[active] ? (
          <ChapterDetail
            key={active}
            manuscriptId={review.manuscriptId}
            chapter={review.chapters[active]}
            overrides={review.overrides}
          />
        ) : (
          <p style={styles.placeholder}>
            Select a chapter to review its structure and low-confidence nodes.
          </p>
        )}
        {review.lowConfidenceNodes === null ? (
          <p style={styles.placeholder}>
            This build's structure was not scored, so nothing here has been checked.
          </p>
        ) : review.lowConfidenceNodes.length > 0 && (
          <LowConfidenceSection nodes={review.lowConfidenceNodes} />
        )}
        {review.orphanedOps !== null && review.orphanedOps.length > 0 && (
          <OrphanedSection orphans={review.orphanedOps} />
        )}
      </div>
    </div>
  );
}

function TargetId({ docxId }: { docxId: string | null }) {
  return docxId === null
    ? <span style={styles.chapterMeta}>untargetable (no source id)</span>
    : <code style={styles.targetId}>{docxId}</code>;
}

function ChapterCard({
  chapter,
  active,
  onClick,
}: {
  chapter: ChapterReview;
  active: boolean;
  onClick: () => void;
}) {
  // Unscored counts as an issue: not measured is not certain.
  const hasIssues = chapter.confidence === null || chapter.confidence < 0.8;
  return (
    <div
      onClick={onClick}
      style={{
        ...styles.chapterCard,
        background: active ? "#e8f4fd" : "#fff",
        borderLeft: hasIssues ? "3px solid #f0ad4e" : "3px solid transparent",
      }}
    >
      <div style={styles.chapterTitle}>
        Ch. {chapter.number ?? "?"}: {chapter.title}
      </div>
      <div style={styles.chapterMeta}>
        Confidence:{" "}
        {chapter.confidence === null ? "unscored" : `${(chapter.confidence * 100).toFixed(0)}%`}
        {hasIssues && <span style={{ color: "#f0ad4e" }}> ⚠</span>}
      </div>
    </div>
  );
}

function ChapterDetail({
  manuscriptId,
  chapter,
  overrides,
}: {
  manuscriptId: string;
  chapter: ChapterReview;
  overrides: OverrideOp[];
}) {
  const aimed = chapter.docxId === null
    ? []
    : overrides.filter((op) => op.sourceRef.docxId === chapter.docxId);
  return (
    <div>
      <h3>
        Ch. {chapter.number ?? "?"}: {chapter.title}
      </h3>
      <p>Target id: <TargetId docxId={chapter.docxId} /></p>
      {aimed.length > 0 && (
        <>
          <h4>Logged overrides ({aimed.length})</h4>
          {aimed.map((op) => <OpLine key={op.id} op={op} />)}
        </>
      )}
      {chapter.docxId !== null && (
        <OverrideForm manuscriptId={manuscriptId} docxId={chapter.docxId} />
      )}
    </div>
  );
}

function OverrideForm({ manuscriptId, docxId }: { manuscriptId: string; docxId: string }) {
  const [state, action, pending] = useActionState(
    submitOverride.bind(null, manuscriptId, docxId),
    null
  );
  return (
    <form action={action} style={styles.form}>
      <select name="op" defaultValue="retitle" aria-label="Operation">
        <option value="retitle">Retitle</option>
        <option value="flag_ambiguity">Flag for review</option>
      </select>
      <input name="text" aria-label="New title or reason" required maxLength={4096} style={{ flex: 1 }} />
      <button type="submit" disabled={pending}>{pending ? "Saving…" : "Log override"}</button>
      {state && (
        <p role="status" style={{ ...styles.chapterMeta, color: state.ok ? "#2e7d32" : "#c62828", width: "100%" }}>
          {state.message}
        </p>
      )}
    </form>
  );
}

function OpLine({ op }: { op: OverrideOp }) {
  const detail = op.op === "reclassify" ? `${op.from} → ${op.to}`
    : op.op === "retitle" ? `"${String(op.value)}"`
    : op.rationale ?? "";
  return (
    <p style={styles.contextText}>
      <strong>{op.op}</strong> {detail} — {op.actor}, {op.at}
    </p>
  );
}

function LowConfidenceSection({ nodes }: { nodes: LowConfidenceNode[] }) {
  return (
    <div style={styles.lowConfSection}>
      <h3>Low-Confidence Nodes ({nodes.length})</h3>
      {nodes.map((node) => (
        <div key={`${node.root}-${node.index}`} style={styles.lowConfCard}>
          <p style={styles.contextText}>"{node.title ?? node.text}"</p>
          <p>
            Read as <strong>{node.type}</strong> in {node.root} (confidence:{" "}
            {(node.confidence * 100).toFixed(0)}%) · <TargetId docxId={node.docxId} />
          </p>
        </div>
      ))}
    </div>
  );
}

function OrphanedSection({ orphans }: { orphans: OrphanedOp[] }) {
  return (
    <div style={styles.lowConfSection}>
      <h3>Overrides with no effect ({orphans.length})</h3>
      <p style={styles.chapterMeta}>
        These logged decisions target no node in this build, so the build skips them.
      </p>
      {orphans.map(({ op }) => (
        <div key={op.id} style={styles.orphanCard}>
          <OpLine op={op} />
          <p style={styles.chapterMeta}>Aimed at <TargetId docxId={op.sourceRef.docxId} /></p>
        </div>
      ))}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    height: "100vh",
    fontFamily: "-apple-system, sans-serif",
  },
  sidebar: {
    width: 280,
    borderRight: "1px solid #e0e0e0",
    padding: 16,
    overflowY: "auto",
    background: "#fafafa",
  },
  main: {
    flex: 1,
    padding: 24,
    overflowY: "auto",
  },
  title: {
    fontSize: 18,
    fontWeight: 600,
    marginBottom: 16,
  },
  chapterList: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  chapterCard: {
    padding: "10px 12px",
    borderRadius: 6,
    cursor: "pointer",
    border: "1px solid #e8e8e8",
    transition: "background 0.15s",
  },
  chapterTitle: {
    fontWeight: 500,
    fontSize: 14,
    marginBottom: 4,
  },
  chapterMeta: {
    fontSize: 12,
    color: "#666",
  },
  placeholder: {
    color: "#999",
    fontStyle: "italic",
    marginTop: 40,
    textAlign: "center",
  },
  orphanCard: {
    background: "#fffbe6",
    border: "1px solid #ffe58f",
    borderRadius: 6,
    padding: 12,
    marginBottom: 8,
  },
  lowConfSection: {
    marginTop: 24,
    borderTop: "1px solid #e8e8e8",
    paddingTop: 16,
  },
  lowConfCard: {
    background: "#fff",
    border: "1px solid #e8e8e8",
    borderRadius: 6,
    padding: 12,
    marginBottom: 8,
  },
  form: {
    display: "flex",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 16,
  },
  targetId: {
    fontSize: 12,
    background: "#f3f3f3",
    padding: "1px 4px",
    borderRadius: 3,
  },
  contextText: {
    fontSize: 13,
    color: "#555",
    fontStyle: "italic",
  },
};
