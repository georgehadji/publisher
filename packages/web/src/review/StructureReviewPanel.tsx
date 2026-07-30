"use client";

import React, { useState, useCallback } from "react";
import type {
  StructureReview,
  ChapterReview,
  LowConfidenceNode,
  OverrideOp,
} from "../types";

/**
 * StructureReviewPanel — the first human gate.
 *
 * Displays:
 * - Chapter map with confidence indicators
 * - Low-confidence nodes requiring classification
 * - Ambiguity flags
 * - Override controls (reclassify, retitle, etc.)
 *
 * From ARCHITECTURE.md §1.1 (Human gate #1):
 * "chapter map, front/back matter, flagged ambiguities"
 */
export function StructureReviewPanel({
  review,
  onOverride,
}: {
  review: StructureReview;
  onOverride?: (op: OverrideOp) => void;
}) {
  const [activeChapter, setActiveChapter] = useState<string | null>(null);

  return (
    <div className="structure-review" style={styles.container}>
      <div style={styles.sidebar}>
        <h2 style={styles.title}>Chapters</h2>
        <div style={styles.chapterList}>
          {review.chapters.map((ch) => (
            <ChapterCard
              key={ch.id}
              chapter={ch}
              active={activeChapter === ch.id}
              onClick={() => setActiveChapter(ch.id)}
            />
          ))}
        </div>
      </div>
      <div style={styles.main}>
        {activeChapter ? (
          <ChapterDetail
            chapter={review.chapters.find((c) => c.id === activeChapter)!}
            onOverride={onOverride}
          />
        ) : (
          <p style={styles.placeholder}>
            Select a chapter to review its structure and low-confidence nodes.
          </p>
        )}
        {review.lowConfidenceNodes.length > 0 && (
          <LowConfidenceSection
            nodes={review.lowConfidenceNodes}
            onOverride={onOverride}
          />
        )}
      </div>
    </div>
  );
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
  const hasIssues = chapter.ambiguities.length > 0 || chapter.confidence < 0.8;
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
        Ch. {chapter.number}: {chapter.title}
      </div>
      <div style={styles.chapterMeta}>
        Confidence: {(chapter.confidence * 100).toFixed(0)}%
        {hasIssues && <span style={{ color: "#f0ad4e" }}> ⚠</span>}
      </div>
    </div>
  );
}

function ChapterDetail({
  chapter,
  onOverride,
}: {
  chapter: ChapterReview;
  onOverride?: (op: OverrideOp) => void;
}) {
  return (
    <div>
      <h3>
        Ch. {chapter.number}: {chapter.title}
      </h3>
      {chapter.ambiguities.map((amb) => (
        <AmbiguityCard key={amb.id} ambiguity={amb} onOverride={onOverride} />
      ))}
    </div>
  );
}

function AmbiguityCard({
  ambiguity,
  onOverride,
}: {
  ambiguity: any;
  onOverride?: (op: OverrideOp) => void;
}) {
  return (
    <div style={styles.ambiguityCard}>
      <p>
        <strong>Flagged:</strong> {ambiguity.message}
      </p>
      <p style={styles.contextText}>{ambiguity.context}</p>
    </div>
  );
}

function LowConfidenceSection({
  nodes,
  onOverride,
}: {
  nodes: LowConfidenceNode[];
  onOverride?: (op: OverrideOp) => void;
}) {
  return (
    <div style={styles.lowConfSection}>
      <h3>Low-Confidence Nodes ({nodes.length})</h3>
      {nodes.map((node, i) => (
        <div key={i} style={styles.lowConfCard}>
          <p style={styles.contextText}>"{node.text}"</p>
          <p>
            Suggested: <strong>{node.suggested}</strong> (confidence:{" "}
            {(node.confidence * 100).toFixed(0)}%)
          </p>
          {node.alternatives?.length > 0 && (
            <div style={styles.alternatives}>
              Alternatives: {node.alternatives.join(", ")}
            </div>
          )}
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
  ambiguityCard: {
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
  contextText: {
    fontSize: 13,
    color: "#555",
    fontStyle: "italic",
  },
  alternatives: {
    fontSize: 12,
    color: "#888",
    marginTop: 4,
  },
};
