import React from "react";
import type { ReviewCardData, ReviewVerdict } from "./cardTypes";
import "./cards.css";

export interface ReviewCardProps {
  review: ReviewCardData;
  className?: string;
}

function getVerdictBadgeStyle(verdict: ReviewVerdict): { bg: string; text: string; border: string } {
  switch (verdict) {
    case "approved":
      return { bg: "var(--badge-success-bg)", text: "var(--accent-green, #3fb950)", border: "var(--accent-green, #2ea043)" };
    case "changes_requested":
      return { bg: "var(--badge-danger-bg)", text: "var(--accent-red, #f85149)", border: "var(--accent-red, #da3633)" };
    case "commented":
    default:
      return { bg: "var(--badge-warning-bg)", text: "var(--accent-yellow, #d29922)", border: "var(--accent-yellow, #bb8009)" };
  }
}

export const ReviewCard: React.FC<ReviewCardProps> = ({
  review,
  className = "",
}) => {
  const verdictStyle = getVerdictBadgeStyle(review.verdict);

  return (
    <div className={`staff-review-card ${className}`}>
      {/* Header: PR and Verdict Badge */}
      <div className="staff-card-header">
        <div className="staff-card-header-left">
          <span className="staff-card-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="18" cy="18" r="3" />
              <circle cx="6" cy="6" r="3" />
              <path d="M13 6h3a2 2 0 0 1 2 2v7" />
              <line x1="6" y1="9" x2="6" y2="21" />
            </svg>
          </span>
          <span className="staff-card-title">
            PR #{review.pr_number} {review.pr_title ? `· ${review.pr_title}` : ""}
          </span>
        </div>
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
            flexShrink: 0,
          }}
        >
          {review.verdict.replace("_", " ")}
        </span>
      </div>

      {/* Summary */}
      {review.summary && (
        <div style={{ fontSize: 13, color: "var(--text-secondary)", margin: "8px 0", lineHeight: 1.5 }}>
          {review.summary}
        </div>
      )}

      {/* Key Findings List */}
      {review.findings && review.findings.length > 0 && (
        <div style={{ marginBottom: 8, padding: "8px 10px", background: "var(--bg-secondary)", borderRadius: "var(--radius-sm, 6px)", border: "1px solid var(--border)" }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", marginBottom: 4 }}>
            Key Findings:
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
            {review.findings.map((finding, idx) => (
              <li key={idx}>{finding}</li>
            ))}
          </ul>
        </div>
      )}

      {/* PR Link */}
      {review.pr_url && (
        <div style={{ marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--border)" }}>
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
