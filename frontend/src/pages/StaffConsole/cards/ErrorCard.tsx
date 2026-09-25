import React from "react";
import type { ErrorCardData } from "./cardTypes";

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
        border: "1px solid rgba(248, 81, 73, 0.4)",
        borderRadius: 8,
        padding: "12px 16px",
        background: "rgba(248, 81, 73, 0.1)",
        maxWidth: 500,
        margin: "6px 0",
        color: "var(--text-primary, #c9d1d9)",
      }}
    >
      {/* Header: Title and optional node badge */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "var(--accent-red, #f85149)" }}>
          ✖ {title}
        </span>
        {error.node && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: "2px 6px",
              borderRadius: 4,
              background: "rgba(255, 255, 255, 0.1)",
              color: "var(--text-secondary, #c9d1d9)",
            }}
          >
            {error.node}
          </span>
        )}
      </div>

      {/* Cause / Detail */}
      {error.cause && (
        <div style={{ fontSize: 12, marginBottom: 8, color: "var(--text-secondary, #c9d1d9)", lineHeight: 1.4 }}>
          {error.cause}
        </div>
      )}

      {/* Remediation instructions */}
      {error.remediation && (
        <div
          style={{
            fontSize: 12,
            background: "rgba(0, 0, 0, 0.25)",
            padding: "8px 10px",
            borderRadius: 4,
            marginBottom: 8,
            borderLeft: "3px solid var(--accent-red, #f85149)",
            lineHeight: 1.4,
          }}
        >
          <strong style={{ color: "var(--text-primary, #c9d1d9)" }}>Remediation:</strong> {error.remediation}
        </div>
      )}

      {/* Retry CTA */}
      {isRetryable && onRetry && (
        <div style={{ marginTop: 4 }}>
          <button
            type="button"
            onClick={onRetry}
            style={{
              background: "var(--accent-red, #f85149)",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "4px 12px",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            ↻ Retry Turn
          </button>
        </div>
      )}
    </div>
  );
};
