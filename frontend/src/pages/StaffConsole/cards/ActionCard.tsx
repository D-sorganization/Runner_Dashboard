import React, { useState } from "react";
import type { ActionProposalData, ActionRiskLevel, ProposalApproveHandler, ProposalDenyHandler } from "./cardTypes";
import "./cards.css";

export interface ActionCardProps {
  proposal: ActionProposalData;
  onApprove?: ProposalApproveHandler;
  onDeny?: ProposalDenyHandler;
  className?: string;
}

function getRiskBadgeColor(risk: ActionRiskLevel): { bg: string; text: string; border: string } {
  switch (risk) {
    case "read":
    case "low":
      return { bg: "var(--badge-success-bg)", text: "var(--accent-green, #3fb950)", border: "var(--accent-green, #3fb950)" };
    case "medium":
      return { bg: "var(--badge-warning-bg)", text: "var(--accent-yellow, #d29922)", border: "var(--accent-yellow, #d29922)" };
    case "high":
    case "critical":
    case "owner-only":
      return { bg: "var(--badge-danger-bg)", text: "var(--accent-red, #f85149)", border: "var(--accent-red, #f85149)" };
    default:
      return { bg: "var(--badge-neutral-bg)", text: "var(--text-muted, #8b949e)", border: "var(--border, #30363d)" };
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

  const reenableIfRefused = (outcome: void | Promise<boolean | void>) => {
    void Promise.resolve(outcome).then((ok) => {
      if (ok === false) setHasSubmitted(false);
    });
  };

  const handleApproveClick = () => {
    if (hasSubmitted || isExpired || isDecided) return;
    setHasSubmitted(true);
    reenableIfRefused(onApprove?.(proposal.id, proposal.params));
  };

  const handleDenyClick = () => {
    if (hasSubmitted || isExpired || isDecided) return;
    setHasSubmitted(true);
    reenableIfRefused(onDeny?.(proposal.id));
  };

  return (
    <div
      className={`staff-action-card ${className}`}
      data-proposal-id={proposal.id}
    >
      {/* Header with Icon, Action Name and Risk Badge */}
      <div className="staff-card-header">
        <div className="staff-card-header-left">
          <span className="staff-card-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
            </svg>
          </span>
          <span className="staff-card-title">{proposal.action_name}</span>
        </div>
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
            flexShrink: 0,
          }}
        >
          {proposal.risk_level}
        </span>
      </div>

      {/* Routed By / Barb Handoff Notice */}
      {proposal.routed_role && (
        <div style={{ fontSize: 11, color: "var(--accent-purple, #bc8cff)", marginBottom: 6 }}>
          🔄 Routed via {proposal.routed_role} to Maintenance
        </div>
      )}

      {/* Key/Value Grid */}
      {(proposal.target || proposal.proposed_by) && (
        <div className="staff-card-grid">
          {proposal.target && (
            <div className="staff-card-kv">
              <span className="staff-card-k">Target</span>
              <span className="staff-card-v">{proposal.target}</span>
            </div>
          )}
          {proposal.proposed_by && (
            <div className="staff-card-kv">
              <span className="staff-card-k">Proposed by</span>
              <span className="staff-card-v">{proposal.proposed_by}</span>
            </div>
          )}
        </div>
      )}

      {proposal.description && (
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8, lineHeight: 1.5 }}>
          {proposal.description}
        </div>
      )}

      {/* Dry Run Preview Banner & Planned Steps */}
      {proposal.dry_run && (
        <div
          data-testid="dry-run-preview"
          style={{
            fontSize: 12,
            color: "var(--accent-blue, #58a6ff)",
            background: "var(--badge-info-bg)",
            border: "1px solid var(--accent-blue, #1f6feb)",
            padding: "8px 12px",
            borderRadius: 6,
            marginBottom: 8,
          }}
        >
          <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span>🔍 Dry-run preview</span>
            {typeof proposal.dry_run === "object" &&
              Array.isArray(proposal.dry_run.planned_steps) &&
              proposal.dry_run.planned_steps.length > 0 && (
                <span style={{ opacity: 0.8, fontSize: 11 }}>
                  ({proposal.dry_run.planned_steps.length} steps)
                </span>
              )}
          </div>
          {typeof proposal.dry_run === "object" &&
            Array.isArray(proposal.dry_run.planned_steps) &&
            proposal.dry_run.planned_steps.length > 0 && (
              <ol style={{ margin: "4px 0 0 18px", padding: 0 }}>
                {proposal.dry_run.planned_steps.map((step, idx) => (
                  <li key={idx} style={{ color: "var(--text-secondary)", margin: "2px 0" }}>
                    {step}
                  </li>
                ))}
              </ol>
            )}
        </div>
      )}

      {/* Decision or Expiry Notice */}
      {isExpired && (
        <div
          style={{
            fontSize: 12,
            color: "var(--accent-red, #f85149)",
            background: "var(--badge-danger-bg)",
            border: "1px solid var(--accent-red, #f85149)",
            padding: "6px 10px",
            borderRadius: 6,
            marginBottom: 8,
          }}
        >
          Proposal expired (24h limit). Actions disabled.
        </div>
      )}

      {isDecided && (
        <div
          style={{
            fontSize: 12,
            color: proposal.status === "denied" ? "var(--accent-red, #f85149)" : "var(--accent-green, #3fb950)",
            background: proposal.status === "denied" ? "var(--badge-danger-bg)" : "var(--badge-success-bg)",
            border: `1px solid ${proposal.status === "denied" ? "var(--accent-red)" : "var(--accent-green)"}`,
            padding: "6px 10px",
            borderRadius: 6,
            marginBottom: 8,
          }}
        >
          <div>
            ✓ {proposal.status.charAt(0).toUpperCase() + proposal.status.slice(1)} by {proposal.decided_by || "user"}
            {proposal.decided_at ? ` at ${new Date(proposal.decided_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
          </div>
          {proposal.verification_message && (
            <div style={{ marginTop: 4, fontWeight: 600, color: "var(--accent-green, #3fb950)" }}>
              ✓ Verified: {proposal.verification_message}
            </div>
          )}
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
                fontFamily: "var(--font-mono, monospace)",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
                padding: "8px 10px",
                borderRadius: 6,
                marginTop: 6,
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
        <div className="staff-action-card__actions">
          <button
            type="button"
            className="staff-action-card__btn staff-action-card__btn--approve"
            onClick={handleApproveClick}
            disabled={hasSubmitted}
          >
            {hasSubmitted ? "Executing…" : "Approve"}
          </button>
          <button
            type="button"
            className="staff-action-card__btn staff-action-card__btn--deny"
            onClick={handleDenyClick}
            disabled={hasSubmitted}
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
};
