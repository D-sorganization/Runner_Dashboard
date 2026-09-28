/**
 * RosterGroup.tsx — Grouped section of staff roles in Roster sidebar.
 *
 * Implements Workstream C (Issue #1721, Epic #1718).
 */
import React from "react";
import type { RosterGroupProps } from "./types";
import { RosterRow } from "./RosterRow";
import "./roster.css";

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
      className="roster-group"
    >
      {/* Group Header */}
      <button
        type="button"
        data-testid={`group-header-${groupKey}`}
        aria-expanded={!isCollapsed}
        aria-controls={`group-content-${groupKey}`}
        onClick={handleHeaderClick}
        onKeyDown={handleKeyDown}
        className="roster-group__header"
      >
        <div className="roster-group__title">
          <span
            className={`roster-group__chevron ${
              isCollapsed ? "roster-group__chevron--collapsed" : ""
            }`}
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 16 16"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M4.22 6.22a.75.75 0 0 1 1.06 0L8 8.94l2.72-2.72a.75.75 0 1 1 1.06 1.06l-3.25 3.25a.75.75 0 0 1-1.06 0L4.22 7.28a.75.75 0 0 1 0-1.06z"
                clipRule="evenodd"
              />
            </svg>
          </span>
          <span>{label}</span>
        </div>

        <span className="roster-group__count">
          {roles.length}
        </span>
      </button>

      {/* Group Content */}
      {!isCollapsed && (
        <div
          id={`group-content-${groupKey}`}
          role="region"
          aria-labelledby={`group-header-${groupKey}`}
          className="roster-group__content"
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

