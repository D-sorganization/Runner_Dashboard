/**
 * RosterRow.tsx — Single role row inside Staff Roster (SC-D3, Issue #1317).
 */

import React from "react";
import type { RosterRowProps } from "./rosterTypes";
import { formatRelativeAge } from "./statusCalculator";

function getRoleInitials(title: string, name: string): string {
  if (name === "barb") return "B";
  const words = (title || name).split(/[\s-_]+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return (title || name).slice(0, 2).toUpperCase();
}

export const RosterRow: React.FC<RosterRowProps> = ({
  role,
  isSelected,
  isPinned,
  isFocused,
  onSelect,
  onTogglePin,
  testIdSuffix = "",
}) => {
  const rowTestId = `roster-row-${role.name}${testIdSuffix ? `-${testIdSuffix}` : ""}`;
  const initials = getRoleInitials(role.title, role.name);
  const relativeAge = role.last_message?.created_at
    ? formatRelativeAge(role.last_message.created_at)
    : "";

  return (
    <div
      role="option"
      aria-selected={isSelected}
      tabIndex={isFocused ? 0 : -1}
      data-testid={rowTestId}
      className={`roster-row ${isSelected ? "roster-row--selected" : ""} ${
        isFocused ? "roster-row--focused" : ""
      } roster-row--${role.status.replace(/\s+/g, "-")}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="roster-row__avatar" aria-hidden="true">
        {initials}
      </div>

      <div className="roster-row__content">
        <div className="roster-row__header">
          <span className="roster-row__name">{role.title || role.name}</span>
          <div className="roster-row__indicators">
            {role.unread_count > 0 && (
              <span
                className="roster-row__unread-badge"
                data-testid={`unread-badge-${role.name}`}
                title={`${role.unread_count} unread messages`}
              >
                {role.unread_count}
              </span>
            )}
            <span
              className={`roster-status-dot roster-status-dot--${role.status.replace(/\s+/g, "-")}`}
              data-testid={`status-dot-${role.name}`}
              data-status={role.status}
              title={role.status_reason}
              aria-label={`Status: ${role.status} — ${role.status_reason}`}
            />
          </div>
        </div>

        <div className="roster-row__footer">
          {role.last_message ? (
            <p className="roster-row__preview" title={role.last_message.body_md}>
              <span className="roster-row__preview-text">{role.last_message.body_md}</span>
              {relativeAge && (
                <span className="roster-row__preview-age"> · {relativeAge}</span>
              )}
            </p>
          ) : (
            <p className="roster-row__summary" title={role.summary}>
              {role.summary || "No active messages"}
            </p>
          )}
        </div>
      </div>

      <button
        type="button"
        className={`roster-row__pin-btn ${isPinned ? "roster-row__pin-btn--pinned" : ""}`}
        data-testid={`pin-btn-${role.name}`}
        aria-label={isPinned ? `Unpin ${role.title || role.name}` : `Pin ${role.title || role.name}`}
        title={isPinned ? "Unpin role" : "Pin role"}
        onClick={(e) => {
          e.stopPropagation();
          onTogglePin();
        }}
      >
        {isPinned ? "★" : "☆"}
      </button>
    </div>
  );
};
