/**
 * HandoffCard.tsx — Structured card for role delegation/handoffs
 * with "send to someone else" redirection controls (SC-D5, Issue #1319).
 */
import React, { useState, useRef } from "react";
import type { HandoffCardProps } from "./cardTypes";

export const HandoffCard: React.FC<HandoffCardProps> = ({
  handoff,
  onRedirectHandoff,
  className = "",
}) => {
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [selectedRole, setSelectedRole] = useState(
    handoff.available_roles?.[0]?.id || ""
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const hasRedirectedRef = useRef(false);

  const formatRoleName = (r: string) =>
    r.charAt(0).toUpperCase() + r.slice(1).replace(/[-_]/g, " ");

  const handleConfirmRedirect = async () => {
    if (hasRedirectedRef.current || isSubmitting || !onRedirectHandoff || !selectedRole) return;
    hasRedirectedRef.current = true;
    setIsSubmitting(true);

    try {
      await onRedirectHandoff(selectedRole);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="region"
      aria-label="Staff handoff"
      data-testid="handoff-card"
      className={`handoff-card ${className}`}
      style={{
        background: "var(--bg-tertiary, #161b22)",
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        padding: "12px 16px",
        margin: "8px 0",
        maxWidth: 540,
        boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
      }}
    >
      {/* Route flow: From -> To */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: "var(--accent-blue, #58a6ff)",
            background: "rgba(56, 139, 253, 0.15)",
            padding: "2px 8px",
            borderRadius: 6,
          }}
        >
          {formatRoleName(handoff.from_role)}
        </span>
        <span style={{ fontSize: 14, color: "var(--text-muted, #8b949e)" }}>➔</span>
        <span
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: "var(--accent-green, #3fb950)",
            background: "rgba(46, 160, 67, 0.15)",
            padding: "2px 8px",
            borderRadius: 6,
          }}
        >
          {formatRoleName(handoff.to_role)}
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 10,
            fontWeight: 600,
            textTransform: "uppercase",
            color: "var(--text-muted, #8b949e)",
            letterSpacing: 0.5,
          }}
        >
          Handoff
        </span>
      </div>

      {/* Reason text */}
      {handoff.reason && (
        <div style={{ fontSize: 12, color: "var(--text-secondary, #c9d1d9)", marginBottom: 8, lineHeight: 1.4 }}>
          {handoff.reason}
        </div>
      )}

      {/* Override / Send to someone else */}
      {onRedirectHandoff && (
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid rgba(48, 54, 61, 0.6)" }}>
          {!isRedirecting ? (
            <button
              type="button"
              data-testid="send-someone-else-btn"
              onClick={() => setIsRedirecting(true)}
              style={{
                background: "none",
                border: "none",
                color: "var(--accent-blue, #58a6ff)",
                cursor: "pointer",
                fontSize: 11,
                fontWeight: 600,
                padding: 0,
              }}
            >
              ⇄ Send to someone else
            </button>
          ) : (
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <select
                data-testid="handoff-role-select"
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value)}
                style={{
                  background: "var(--bg-primary, #0d1117)",
                  color: "var(--text-primary, #c9d1d9)",
                  border: "1px solid var(--border, #30363d)",
                  borderRadius: 4,
                  fontSize: 11,
                  padding: "4px 8px",
                }}
              >
                {(handoff.available_roles || []).map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                data-testid="confirm-redirect-btn"
                disabled={isSubmitting || !selectedRole}
                onClick={handleConfirmRedirect}
                style={{
                  background: "var(--accent-blue, #1f6feb)",
                  color: "#fff",
                  border: "none",
                  borderRadius: 4,
                  padding: "4px 10px",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: isSubmitting ? "not-allowed" : "pointer",
                }}
              >
                Redirect
              </button>
              <button
                type="button"
                onClick={() => setIsRedirecting(false)}
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--text-muted, #8b949e)",
                  cursor: "pointer",
                  fontSize: 11,
                  padding: "4px 6px",
                }}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
