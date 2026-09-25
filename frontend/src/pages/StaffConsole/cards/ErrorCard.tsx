/**
 * ErrorCard.tsx — Classified error card with plain-language cause,
 * node details, remediation command with copy button, and retry gate (SC-D5, Issue #1319).
 */
import React, { useState, useRef } from "react";
import type { ErrorCardProps } from "./cardTypes";
import { formatFailureTitle } from "../threadUtils";

export const ErrorCard: React.FC<ErrorCardProps> = ({
  error,
  onRetry,
  className = "",
}) => {
  const [copied, setCopied] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const hasRetriedRef = useRef(false);

  const title = formatFailureTitle(error.failure_class);
  const isRetryable = error.retryable ?? Boolean(onRetry);

  const handleCopyCommand = async () => {
    if (!error.command) return;
    try {
      await navigator.clipboard.writeText(error.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback or ignore in non-browser environments
    }
  };

  const handleRetry = async () => {
    if (hasRetriedRef.current || isRetrying || !onRetry) return;
    hasRetriedRef.current = true;
    setIsRetrying(true);

    try {
      await onRetry();
    } finally {
      setIsRetrying(false);
    }
  };

  return (
    <div
      role="alert"
      aria-label="Error card"
      data-testid="error-card"
      className={`staff-error-card ${className}`}
      style={{
        background: "rgba(248, 81, 73, 0.1)",
        border: "1px solid rgba(248, 81, 73, 0.4)",
        borderRadius: 8,
        padding: "14px 16px",
        margin: "8px 0",
        maxWidth: 540,
        boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
      }}
    >
      {/* Title & Failure Class */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: "var(--accent-red, #f85149)", fontWeight: 700, fontSize: 13 }}>
            ✖ {title}
          </span>
        </div>
        {error.node && (
          <span
            style={{
              fontSize: 10,
              padding: "2px 6px",
              borderRadius: 4,
              background: "rgba(0,0,0,0.2)",
              color: "var(--text-muted, #8b949e)",
              fontFamily: "monospace",
            }}
          >
            Node: {error.node}
          </span>
        )}
      </div>

      {/* Message explanation */}
      {error.message && (
        <div style={{ fontSize: 12, color: "var(--text-secondary, #c9d1d9)", marginBottom: 8, lineHeight: 1.4 }}>
          {error.message}
        </div>
      )}

      {/* Remediation & Command */}
      {(error.remediation || error.command) && (
        <div
          style={{
            background: "rgba(0, 0, 0, 0.2)",
            borderLeft: "3px solid var(--accent-red, #f85149)",
            borderRadius: 4,
            padding: "8px 10px",
            marginBottom: 10,
            fontSize: 12,
          }}
        >
          {error.remediation && (
            <div style={{ color: "var(--text-primary, #c9d1d9)", marginBottom: error.command ? 6 : 0 }}>
              <strong>Remediation:</strong> {error.remediation}
            </div>
          )}

          {error.command && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
              <code
                style={{
                  flex: 1,
                  background: "var(--bg-primary, #0d1117)",
                  padding: "4px 8px",
                  borderRadius: 4,
                  fontSize: 11,
                  color: "var(--accent-yellow, #d29922)",
                  overflowX: "auto",
                  fontFamily: "monospace",
                }}
              >
                {error.command}
              </code>
              <button
                type="button"
                data-testid="copy-cmd-btn"
                onClick={handleCopyCommand}
                style={{
                  background: "rgba(255, 255, 255, 0.1)",
                  border: "1px solid var(--border, #30363d)",
                  borderRadius: 4,
                  color: "var(--text-secondary, #c9d1d9)",
                  fontSize: 11,
                  padding: "4px 8px",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                {copied ? "✓ Copied" : "Copy"}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Retry Action */}
      {isRetryable && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          <button
            type="button"
            data-testid="retry-turn-btn"
            disabled={isRetrying || !onRetry}
            onClick={handleRetry}
            style={{
              background: "var(--accent-red, #f85149)",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "4px 14px",
              fontSize: 12,
              fontWeight: 600,
              cursor: isRetrying ? "not-allowed" : "pointer",
              opacity: isRetrying ? 0.6 : 1,
            }}
          >
            ↻ Retry Turn
          </button>
        </div>
      )}
    </div>
  );
};
