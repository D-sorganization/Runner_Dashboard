import React, { useState } from "react";
import type { HandoffCardData } from "./cardTypes";
import "./cards.css";

export interface HandoffCardProps {
  handoff: HandoffCardData;
  onReroute?: (targetRole: string) => void;
  /** Open (or continue) the target role's thread (#1548). */
  onFollow?: (targetRole: string) => void;
  className?: string;
}

const roleLabel = (role: string): string => role.charAt(0).toUpperCase() + role.slice(1);

export const HandoffCard: React.FC<HandoffCardProps> = ({
  handoff,
  onReroute,
  onFollow,
  className = "",
}) => {
  const [showOverride, setShowOverride] = useState(false);

  return (
    <div className={`staff-handoff-card ${className}`}>
      {/* Header with Routing Transition */}
      <div className="staff-card-header">
        <div className="staff-card-header-left">
          <span className="staff-card-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="17 1 21 5 17 9" />
              <path d="M3 11V9a4 4 0 0 1 4-4h14" />
              <polyline points="7 23 3 19 7 15" />
              <path d="M21 13v2a4 4 0 0 1-4 4H3" />
            </svg>
          </span>
          <span className="staff-card-title">Role Handoff</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600 }}>
          <span style={{ color: "var(--accent-blue, #58a6ff)" }}>{roleLabel(handoff.from_role)}</span>
          <span style={{ color: "var(--text-muted)" }}>→</span>
          <span style={{ color: "var(--accent-purple, #bc8cff)" }}>{roleLabel(handoff.to_role)}</span>
        </div>
      </div>

      {/* Rationale */}
      <div style={{ fontSize: 13, color: "var(--text-secondary)", margin: "8px 0", lineHeight: 1.5 }}>
        {handoff.reason}
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--border)" }}>
        {onFollow && (
          <button
            type="button"
            onClick={() => onFollow(handoff.to_role)}
            className="staff-action-card__btn staff-action-card__btn--approve"
            style={{ padding: "4px 10px", fontSize: 11 }}
          >
            Continue with {roleLabel(handoff.to_role)}
          </button>
        )}

        {onReroute && (
          <div>
            <button
              type="button"
              onClick={() => setShowOverride((prev) => !prev)}
              className="staff-action-card__btn staff-action-card__btn--deny"
              style={{ padding: "4px 10px", fontSize: 11, color: "var(--text-secondary)" }}
            >
              {showOverride ? "Cancel re-routing" : "Send to someone else"}
            </button>

            {showOverride && handoff.available_alternatives && handoff.available_alternatives.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                {handoff.available_alternatives.map((alt) => (
                  <button
                    key={alt.name}
                    type="button"
                    onClick={() => onReroute(alt.name)}
                    className="staff-action-card__btn"
                    style={{
                      background: "var(--badge-info-bg)",
                      border: "1px solid var(--accent-blue)",
                      color: "var(--accent-blue)",
                      fontSize: 11,
                      padding: "3px 8px",
                    }}
                  >
                    {alt.title || alt.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
