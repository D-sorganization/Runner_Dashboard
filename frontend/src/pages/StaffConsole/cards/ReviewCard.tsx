import React from "react";
import type { ReviewCardData, ReviewVerdict } from "./cardTypes";

export interface ReviewCardProps {
  review: ReviewCardData;
  className?: string;
}

function getVerdictBadgeStyle(verdict: ReviewVerdict): { bg: string; text: string; border: string } {
  switch (verdict) {
    case "approved":
      return { bg: "rgba(46, 160, 67, 0.15)", text: "var(--accent-green, #3fb950)", border: "var(--border-green, #2ea043)" };
    case "changes_requested":
      return { bg: "rgba(248, 81, 73, 0.15)", text: "var(--accent-red, #f85149)", border: "var(--border-red, #da3633)" };
    case "commented":
    default:
      return { bg: "rgba(210, 153, 34, 0.15)", text: "var(--accent-yellow, #d29922)", border: "var(--border-yellow, #bb8009)" };
  }
}

export const ReviewCard: React.FC<ReviewCardProps> = ({
  review,
  className = "",
}) => {
  const verdictStyle = getVerdictBadgeStyle(review.verdict);

  return (
    <div
      className={`staff-review-card ${className}`}
      style={{
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        padding: "12px 16px",
        background: "var(--bg-secondary, #161b22)",
        maxWidth: 500,
        margin: "6px 0",
      }}
    >
      {/* Header: PR and Verdict Badge */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary, #c9d1d9)" }}>
          PR #{review.pr_number} {review.pr_title ? `· ${review.pr_title}` : ""}
        </span>
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            fontWeight: 700,
            padding: "2px 6px",
            borderRadius: 4,
            background: verdictStyle.bg,
            color: verdictStyle.text,
            border: `1px solid ${verdictStyle.border}`,
          }}
        >
          {review.verdict.replace("_", " ")}
        </span>
      </div>

      {/* Summary */}
      {review.summary && (
        <div style={{ fontSize: 12, color: "var(--text-secondary, #c9d1d9)", marginBottom: 8, lineHeight: 1.4 }}>
          {review.summary}
        </div>
      )}

      {/* Key Findings List */}
      {review.findings && review.findings.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted, #8b949e)", marginBottom: 4 }}>
            Key Findings:
          </div>
          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12, color: "var(--text-secondary, #c9d1d9)", lineHeight: 1.5 }}>
            {review.findings.map((finding, idx) => (
              <li key={idx}>{finding}</li>
            ))}
          </ul>
        </div>
      )}

      {/* PR Link */}
      {review.pr_url && (
        <div style={{ marginTop: 4 }}>
          <a
            href={review.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: "var(--accent-purple, #bc8cff)",
              fontSize: 12,
              textDecoration: "none",
              fontWeight: 500,
            }}
          >
            View PR #{review.pr_number}
          </a>
        </div>
      )}
    </div>
  );
};
