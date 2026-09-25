import React, { useState } from "react";
import type { ActionProposalData, ActionRiskLevel } from "./cardTypes";

export interface ActionCardProps {
  proposal: ActionProposalData;
  onApprove?: (proposalId: string, params?: Record<string, unknown>) => void;
  onDeny?: (proposalId: string) => void;
  className?: string;
}

function getRiskBadgeColor(risk: ActionRiskLevel): { bg: string; text: string; border: string } {
  switch (risk) {
    case "read":
    case "low":
      return { bg: "rgba(46, 160, 67, 0.15)", text: "var(--accent-green, #3fb950)", border: "var(--border-green, #2ea043)" };
    case "medium":
      return { bg: "rgba(210, 153, 34, 0.15)", text: "var(--accent-yellow, #d29922)", border: "var(--border-yellow, #bb8009)" };
    case "high":
    case "critical":
    case "owner-only":
      return { bg: "rgba(248, 81, 73, 0.15)", text: "var(--accent-red, #f85149)", border: "var(--border-red, #da3633)" };
    default:
      return { bg: "rgba(110, 118, 129, 0.15)", text: "var(--text-muted, #8b949e)", border: "var(--border-muted, #6e7681)" };
  }
}

export const ActionCard: React.FC<ActionCardProps> = ({
  proposal,
  onApprove,
  onDeny,
  className = "",
}) => {
  const [showParams, setShowParams] = useState(false);
  const [hasSubmitted, setHasSubmitted] = useState(false);

  const isExpired =
    proposal.status === "expired" ||
    Boolean(proposal.expires_at && new Date(proposal.expires_at).getTime() < Date.now());

  const isDecided =
    proposal.status === "approved" ||
    proposal.status === "denied" ||
    proposal.status === "executed";

  const riskColors = getRiskBadgeColor(proposal.risk_level);

  const handleApproveClick = () => {
    if (hasSubmitted || isExpired || isDecided) return;
    setHasSubmitted(true);
    onApprove?.(proposal.id, proposal.params);
  };

  const handleDenyClick = () => {
    if (hasSubmitted || isExpired || isDecided) return;
    setHasSubmitted(true);
    onDeny?.(proposal.id);
  };

  return (
    <div
      className={`staff-action-card ${className}`}
      data-proposal-id={proposal.id}
      style={{
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        padding: "12px 16px",
        background: "var(--bg-secondary, #161b22)",
        maxWidth: 500,
        margin: "6px 0",
      }}
    >
      {/* Header with Action Name and Risk Badge */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary, #c9d1d9)" }}>
          {proposal.action_name}
        </span>
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            fontWeight: 700,
            padding: "2px 6px",
            borderRadius: 4,
            background: riskColors.bg,
            color: riskColors.text,
            border: `1px solid ${riskColors.border}`,
          }}
        >
          {proposal.risk_level}
        </span>
      </div>

      {/* Target and Description */}
      {proposal.target && (
        <div style={{ fontSize: 12, color: "var(--text-muted, #8b949e)", marginBottom: 4 }}>
          Target: <strong style={{ color: "var(--text-secondary, #c9d1d9)" }}>{proposal.target}</strong>
        </div>
      )}

      {proposal.description && (
        <div style={{ fontSize: 12, color: "var(--text-secondary, #c9d1d9)", marginBottom: 8, lineHeight: 1.4 }}>
          {proposal.description}
        </div>
      )}

      {/* Decision or Expiry Notice */}
      {isExpired && (
        <div
          style={{
            fontSize: 11,
            color: "var(--accent-red, #f85149)",
            background: "rgba(248, 81, 73, 0.1)",
            padding: "4px 8px",
            borderRadius: 4,
            marginBottom: 8,
          }}
        >
          ⚠ Proposal expired (24h limit). Actions disabled.
        </div>
      )}

      {isDecided && (
        <div
          style={{
            fontSize: 11,
            color: proposal.status === "denied" ? "var(--accent-red, #f85149)" : "var(--accent-green, #3fb950)",
            background: "rgba(255, 255, 255, 0.05)",
            padding: "4px 8px",
            borderRadius: 4,
            marginBottom: 8,
          }}
        >
          ✓ {proposal.status.charAt(0).toUpperCase() + proposal.status.slice(1)} by {proposal.decided_by || "user"}
          {proposal.decided_at ? ` at ${new Date(proposal.decided_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
        </div>
      )}

      {/* Params toggle and inspection */}
      {proposal.params && Object.keys(proposal.params).length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <button
            type="button"
            onClick={() => setShowParams((prev) => !prev)}
            style={{
              background: "none",
              border: "none",
              color: "var(--accent-blue, #58a6ff)",
              fontSize: 11,
              padding: 0,
              cursor: "pointer",
              textDecoration: "underline",
            }}
          >
            {showParams ? "Hide params" : "View params"}
          </button>
          {showParams && (
            <pre
              style={{
                fontSize: 11,
                background: "rgba(0, 0, 0, 0.3)",
                padding: "6px 8px",
                borderRadius: 4,
                marginTop: 4,
                overflowX: "auto",
              }}
            >
              {JSON.stringify(proposal.params, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* Actions (Approve / Deny) */}
      {!isDecided && !isExpired && (
        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            onClick={handleApproveClick}
            disabled={hasSubmitted}
            style={{
              background: "var(--accent-green, #238636)",
              color: "var(--color-fg-on-emphasis, #ffffff)",
              border: "1px solid rgba(240, 246, 252, 0.1)",
              borderRadius: 6,
              padding: "4px 12px",
              fontSize: 12,
              fontWeight: 600,
              cursor: hasSubmitted ? "not-allowed" : "pointer",
              opacity: hasSubmitted ? 0.6 : 1,
            }}
          >
            {hasSubmitted ? "Executing…" : "Approve"}
          </button>
          <button
            type="button"
            onClick={handleDenyClick}
            disabled={hasSubmitted}
            style={{
              background: "transparent",
              color: "var(--accent-red, #f85149)",
              border: "1px solid var(--border-red, #da3633)",
              borderRadius: 6,
              padding: "4px 12px",
              fontSize: 12,
              fontWeight: 600,
              cursor: hasSubmitted ? "not-allowed" : "pointer",
              opacity: hasSubmitted ? 0.6 : 1,
            }}
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
};
