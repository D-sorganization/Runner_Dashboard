/**
 * ReviewCard.tsx — Structured card for pull request reviews: PR info,
 * verdict badge, summary, and key findings (SC-D5, Issue #1319).
 */
import React from "react";
import type { ReviewCardProps } from "./cardTypes";
import { getVerdictBadgeStyle, formatCardDateTime } from "./cardUtils";

export const ReviewCard: React.FC<ReviewCardProps> = ({
  review,
  className = "",
}) => {
  const verdictStyle = getVerdictBadgeStyle(review.verdict);

  return (
    <div
      role="region"
      aria-label="Code review verdict"
      data-testid="review-card"
      className={`review-verdict-card ${className}`}
      style={{
        background: "var(--bg-tertiary, #161b22)",
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        padding: "14px 16px",
        margin: "8px 0",
        maxWidth: 540,
        boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
      }}
    >
      {/* Header: PR info & Verdict badge */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--text-muted, #8b949e)" }}>
            Pull Request Review
          </div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary, #c9d1d9)", marginTop: 2 }}>
            {review.pr_number && (
              <a
                href={review.pr_url || `#`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "underline", marginRight: 6 }}
              >
                #{review.pr_number}
              </a>
            )}
            {review.pr_title && <span>{review.pr_title}</span>}
          </div>
        </div>
        <span
          data-testid="review-verdict"
          style={{
            fontSize: 11,
            fontWeight: 700,
            padding: "3px 10px",
            borderRadius: 12,
            background: verdictStyle.bg,
            color: verdictStyle.color,
            border: verdictStyle.border,
            textTransform: "uppercase",
            whiteSpace: "nowrap",
          }}
        >
          {review.verdict}
        </span>
      </div>

      {/* Reviewer & timestamp */}
      {(review.reviewer || review.reviewed_at) && (
        <div style={{ fontSize: 11, color: "var(--text-muted, #8b949e)", marginBottom: 8 }}>
          {review.reviewer && <span>Reviewed by <strong>{review.reviewer}</strong></span>}
          {review.reviewed_at && <span> on {formatCardDateTime(review.reviewed_at)}</span>}
        </div>
      )}

      {/* Summary */}
      {review.summary && (
        <div style={{ fontSize: 12, color: "var(--text-secondary, #c9d1d9)", marginBottom: 10, lineHeight: 1.4 }}>
          {review.summary}
        </div>
      )}

      {/* Key findings */}
      {review.findings && review.findings.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted, #8b949e)", marginBottom: 4, textTransform: "uppercase" }}>
            Key Findings:
          </div>
          <ul
            data-testid="review-findings"
            style={{
              margin: 0,
              paddingLeft: 18,
              fontSize: 12,
              color: "var(--text-primary, #c9d1d9)",
              lineHeight: 1.5,
            }}
          >
            {review.findings.map((item, idx) => (
              <li key={`finding-${idx}`} style={{ marginBottom: 2 }}>
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
