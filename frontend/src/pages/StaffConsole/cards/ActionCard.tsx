/**
 * ActionCard.tsx — Action proposal card with risk badge, approval gates,
 * double-click idempotency protection, and param editing (SC-D5, Issue #1319).
 */
import React, { useState, useRef } from "react";
import type { ActionCardProps } from "./cardTypes";
import { getRiskBadgeStyle, getStatusBadgeStyle, formatCardDateTime } from "./cardUtils";

export const ActionCard: React.FC<ActionCardProps> = ({
  proposal,
  onApprove,
  onDeny,
  disabled = false,
  className = "",
}) => {
  const [isEditingParams, setIsEditingParams] = useState(false);
  const [paramsText, setParamsText] = useState(() => JSON.stringify(proposal.params, null, 2));
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);

  const hasExecutedRef = useRef(false);

  const isExpired = proposal.is_expired || proposal.state === "expired";
  const isDecided = proposal.state !== "proposed" && !isExpired;
  const isActionable = proposal.state === "proposed" && !isExpired && !disabled;

  const riskStyle = getRiskBadgeStyle(proposal.risk);
  const statusStyle = getStatusBadgeStyle(proposal.state);

  const handleApprove = async () => {
    if (hasExecutedRef.current || isSubmitting || !onApprove) return;
    hasExecutedRef.current = true;
    setIsSubmitting(true);

    let parsedParams = proposal.params;
    if (isEditingParams) {
      try {
        parsedParams = JSON.parse(paramsText);
      } catch (err) {
        setParseError("Invalid JSON parameters");
        hasExecutedRef.current = false;
        setIsSubmitting(false);
        return;
      }
    }

    try {
      await onApprove(proposal.id, parsedParams);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeny = async () => {
    if (hasExecutedRef.current || isSubmitting || !onDeny) return;
    hasExecutedRef.current = true;
    setIsSubmitting(true);

    try {
      await onDeny(proposal.id, undefined);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="region"
      aria-label="Action proposal"
      data-testid="action-card"
      className={`action-proposal-card ${className}`}
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
      {/* Header: Action name and Risk Badge */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--text-muted, #8b949e)" }}>
            Action Proposal
          </div>
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary, #c9d1d9)", marginTop: 2 }}>
            {proposal.action}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              padding: "2px 8px",
              borderRadius: 12,
              background: riskStyle.bg,
              color: riskStyle.color,
              border: riskStyle.border,
              textTransform: "uppercase",
            }}
          >
            {proposal.risk} risk
          </span>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "2px 8px",
              borderRadius: 12,
              background: statusStyle.bg,
              color: statusStyle.color,
              border: statusStyle.border,
              textTransform: "capitalize",
            }}
          >
            {proposal.state}
          </span>
        </div>
      </div>

      {/* Target details */}
      {proposal.target && (
        <div style={{ fontSize: 12, color: "var(--text-secondary, #8b949e)", marginBottom: 8 }}>
          <strong>Target:</strong> <span style={{ color: "var(--text-primary, #c9d1d9)" }}>{proposal.target}</span>
        </div>
      )}

      {/* Decision info or Expiration explanation */}
      {isExpired && (
        <div
          role="status"
          style={{
            fontSize: 12,
            padding: "6px 10px",
            background: "rgba(139, 148, 158, 0.15)",
            border: "1px solid rgba(139, 148, 158, 0.3)",
            borderRadius: 6,
            color: "var(--text-muted, #8b949e)",
            marginBottom: 8,
          }}
        >
          ⏱ <em>Proposal expired — actions disabled.</em>
        </div>
      )}

      {isDecided && proposal.decided_by && (
        <div
          role="status"
          style={{
            fontSize: 12,
            padding: "6px 10px",
            background: "rgba(0, 0, 0, 0.2)",
            borderLeft: "3px solid var(--accent-blue, #58a6ff)",
            borderRadius: 4,
            color: "var(--text-secondary, #8b949e)",
            marginBottom: 8,
          }}
        >
          Decided: {proposal.state} by {proposal.decided_by}
          {proposal.decided_at && ` at ${formatCardDateTime(proposal.decided_at)}`}
          {proposal.reason && ` ("${proposal.reason}")`}
        </div>
      )}

      {/* Parameters View / Edit */}
      <div style={{ margin: "10px 0" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted, #8b949e)" }}>Parameters:</span>
          {isActionable && (
            <button
              type="button"
              data-testid="edit-params-btn"
              onClick={() => setIsEditingParams(!isEditingParams)}
              style={{
                background: "none",
                border: "none",
                color: "var(--accent-blue, #58a6ff)",
                cursor: "pointer",
                fontSize: 11,
                padding: 0,
              }}
            >
              {isEditingParams ? "Cancel Edit" : "✎ Edit params"}
            </button>
          )}
        </div>

        {isEditingParams ? (
          <div>
            <textarea
              data-testid="params-textarea"
              value={paramsText}
              onChange={(e) => {
                setParamsText(e.target.value);
                setParseError(null);
              }}
              rows={4}
              style={{
                width: "100%",
                fontFamily: "monospace",
                fontSize: 11,
                background: "var(--bg-primary, #0d1117)",
                color: "var(--text-primary, #c9d1d9)",
                border: "1px solid var(--border, #30363d)",
                borderRadius: 4,
                padding: 8,
                resize: "vertical",
              }}
            />
            {parseError && (
              <div style={{ color: "var(--accent-red, #f85149)", fontSize: 11, marginTop: 2 }}>{parseError}</div>
            )}
          </div>
        ) : (
          <pre
            style={{
              margin: 0,
              padding: "6px 8px",
              background: "var(--bg-primary, #0d1117)",
              borderRadius: 4,
              fontSize: 11,
              fontFamily: "monospace",
              color: "var(--text-secondary, #8b949e)",
              maxHeight: 120,
              overflowY: "auto",
            }}
          >
            {JSON.stringify(proposal.params, null, 2)}
          </pre>
        )}
      </div>

      {/* Action Buttons with double-click idempotency */}
      {isActionable && (
        <div style={{ display: "flex", gap: 10, marginTop: 12, justifyContent: "flex-end" }}>
          <button
            type="button"
            data-testid="deny-btn"
            disabled={isSubmitting}
            onClick={handleDeny}
            style={{
              background: "transparent",
              color: "var(--accent-red, #f85149)",
              border: "1px solid var(--accent-red, #f85149)",
              borderRadius: 6,
              padding: "6px 14px",
              fontSize: 12,
              fontWeight: 600,
              cursor: isSubmitting ? "not-allowed" : "pointer",
              opacity: isSubmitting ? 0.6 : 1,
            }}
          >
            ✕ Deny
          </button>
          <button
            type="button"
            data-testid="approve-btn"
            disabled={isSubmitting}
            onClick={handleApprove}
            style={{
              background: "var(--accent-green, #238636)",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "6px 16px",
              fontSize: 12,
              fontWeight: 600,
              cursor: isSubmitting ? "not-allowed" : "pointer",
              opacity: isSubmitting ? 0.6 : 1,
            }}
          >
            ✓ Approve
          </button>
        </div>
      )}
    </div>
  );
};
