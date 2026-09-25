import React, { useState } from "react";
import type { HandoffCardData } from "./cardTypes";

export interface HandoffCardProps {
  handoff: HandoffCardData;
  onReroute?: (targetRole: string) => void;
  className?: string;
}

export const HandoffCard: React.FC<HandoffCardProps> = ({
  handoff,
  onReroute,
  className = "",
}) => {
  const [showOverride, setShowOverride] = useState(false);

  return (
    <div
      className={`staff-handoff-card ${className}`}
      style={{
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        padding: "12px 16px",
        background: "var(--bg-secondary, #161b22)",
        maxWidth: 500,
        margin: "6px 0",
      }}
    >
      {/* Routing Transition */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, fontSize: 13, fontWeight: 600 }}>
        <span style={{ color: "var(--accent-blue, #58a6ff)" }}>
          {handoff.from_role.charAt(0).toUpperCase() + handoff.from_role.slice(1)}
        </span>
        <span style={{ color: "var(--text-muted, #8b949e)" }}>→</span>
        <span style={{ color: "var(--accent-purple, #bc8cff)" }}>
          {handoff.to_role.charAt(0).toUpperCase() + handoff.to_role.slice(1)}
        </span>
      </div>

      {/* Rationale */}
      <div style={{ fontSize: 12, color: "var(--text-secondary, #c9d1d9)", marginBottom: 8, lineHeight: 1.4 }}>
        {handoff.reason}
      </div>

      {/* Re-route / Override Action */}
      {onReroute && (
        <div>
          <button
            type="button"
            onClick={() => setShowOverride((prev) => !prev)}
            style={{
              background: "none",
              border: "1px solid var(--border, #30363d)",
              color: "var(--text-secondary, #c9d1d9)",
              fontSize: 11,
              padding: "3px 8px",
              borderRadius: 4,
              cursor: "pointer",
            }}
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
                  style={{
                    background: "rgba(56, 139, 253, 0.15)",
                    border: "1px solid var(--border-blue, #1f6feb)",
                    color: "var(--accent-blue, #58a6ff)",
                    fontSize: 11,
                    padding: "3px 8px",
                    borderRadius: 4,
                    cursor: "pointer",
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
  );
};
