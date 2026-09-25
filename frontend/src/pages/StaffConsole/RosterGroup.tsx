/**
 * RosterGroup.tsx — Grouped section of staff roles in Roster sidebar.
 *
 * Implements SC-D3 (Issue #1317) under Epic SC-D (#1350).
 */
import React from "react";
import type { RosterGroupProps } from "./types";
import { RosterRow } from "./RosterRow";

export const RosterGroup: React.FC<RosterGroupProps> = ({
  groupKey,
  label,
  roles,
  isCollapsed,
  onToggleCollapse,
  selectedRoleId,
  pinnedRoleIds,
  onSelectRole,
  onTogglePin,
  availableProviders,
  focusedRoleId,
}) => {
  if (roles.length === 0) {
    return null;
  }

  const handleHeaderClick = () => {
    onToggleCollapse(groupKey);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggleCollapse(groupKey);
    }
  };

  return (
    <div
      data-testid={`roster-group-${groupKey}`}
      style={{
        marginBottom: "12px",
      }}
    >
      {/* Group Header */}
      <button
        type="button"
        data-testid={`group-header-${groupKey}`}
        aria-expanded={!isCollapsed}
        aria-controls={`group-content-${groupKey}`}
        onClick={handleHeaderClick}
        onKeyDown={handleKeyDown}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "6px 8px",
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "var(--text-secondary, #8b949e)",
          fontSize: "11px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          borderRadius: "4px",
          textAlign: "left",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span
            style={{
              fontSize: "10px",
              display: "inline-block",
              transform: isCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
              transition: "transform 0.15s ease",
            }}
          >
            ▼
          </span>
          <span>{label}</span>
        </div>

        <span
          style={{
            fontSize: "10px",
            backgroundColor: "var(--bg-tertiary, #1c2333)",
            color: "var(--text-muted, #868e98)",
            padding: "1px 6px",
            borderRadius: "10px",
            fontWeight: 600,
          }}
        >
          {roles.length}
        </span>
      </button>

      {/* Group Content */}
      {!isCollapsed && (
        <div
          id={`group-content-${groupKey}`}
          role="region"
          aria-labelledby={`group-header-${groupKey}`}
          style={{
            paddingLeft: "4px",
            marginTop: "2px",
          }}
        >
          {roles.map((role) => (
            <RosterRow
              key={`${groupKey}-${role.name}`}
              role={role}
              isSelected={selectedRoleId === role.name}
              isPinned={pinnedRoleIds.includes(role.name)}
              isFocused={focusedRoleId === role.name}
              onSelect={onSelectRole}
              onTogglePin={onTogglePin}
              availableProviders={availableProviders}
              isAutoRoute={role.name === "barb" && groupKey === "leadership"}
            />
          ))}
        </div>
      )}
    </div>
  );
};
