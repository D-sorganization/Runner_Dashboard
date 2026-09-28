import React from "react";
import type { ErrorCardData } from "./cardTypes";
import "./cards.css";

export interface ErrorCardProps {
  error: ErrorCardData;
  onRetry?: () => void;
  className?: string;
}

function getFailureClassTitle(failureClass?: string | null): string {
  switch (failureClass) {
    case "auth_expired":
      return "Authentication Expired";
    case "cli_missing":
      return "CLI Tool Missing";
    case "cli_outdated":
      return "CLI Tool Outdated";
    case "needs_input":
      return "Input Required";
    case "timeout":
      return "Operation Timed Out";
    case "stalled":
      return "Execution Stalled";
    case "lease_blocked":
      return "Lease Contention Blocked";
    case "orphaned":
      return "Orphaned Process";
    default:
      return failureClass ? failureClass.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "System Failure";
  }
}

export const ErrorCard: React.FC<ErrorCardProps> = ({
  error,
  onRetry,
  className = "",
}) => {
  const title = getFailureClassTitle(error.failure_class);
  const isRetryable = error.retryable !== false;

  return (
    <div
      role="alert"
      className={`staff-error-card ${className}`}
      style={{
        border: "1px solid var(--accent-red)",
        background: "var(--badge-danger-bg)",
      }}
    >
      {/* Header: Title and optional node badge */}
      <div className="staff-card-header">
        <div className="staff-card-header-left">
          <span className="staff-card-icon" style={{ color: "var(--accent-red)" }} aria-hidden="true">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="15" y1="9" x2="9" y2="15" />
              <line x1="9" y1="9" x2="15" y2="15" />
            </svg>
          </span>
          <span className="staff-card-title" style={{ color: "var(--accent-red)" }}>
            {title}
          </span>
        </div>
        {error.node && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: "2px 6px",
              borderRadius: 4,
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              color: "var(--text-secondary)",
            }}
          >
            {error.node}
          </span>
        )}
      </div>

      {/* Cause / Detail */}
      {error.cause && (
        <div style={{ fontSize: 13, marginBottom: 8, color: "var(--text-primary)", lineHeight: 1.5 }}>
          {error.cause}
        </div>
      )}

      {/* Remediation instructions */}
      {error.remediation && (
        <div
          style={{
            fontSize: 12,
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderLeft: "3px solid var(--accent-red)",
            padding: "8px 10px",
            borderRadius: "var(--radius-sm, 6px)",
            marginBottom: 8,
            lineHeight: 1.45,
          }}
        >
          <strong style={{ color: "var(--text-primary)" }}>Remediation:</strong> {error.remediation}
        </div>
      )}

      {/* Retry CTA */}
      {isRetryable && onRetry && (
        <div style={{ marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--border)" }}>
          <button
            type="button"
            onClick={onRetry}
            className="staff-action-card__btn"
            style={{
              background: "var(--accent-red)",
              color: "var(--text-on-accent)",
              border: "none",
              padding: "4px 12px",
            }}
          >
            ↻ Retry Turn
          </button>
        </div>
      )}
    </div>
  );
};
