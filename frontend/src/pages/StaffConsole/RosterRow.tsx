/**
 * RosterRow.tsx — Individual role entry in the Staff Console Roster sidebar.
 *
 * Implements Workstream C (Issue #1721, Epic #1718).
 */
import React from "react";
import type { RosterRowProps, RosterStatus } from "./types";
import { computeRoleStatus, formatRelativeTime, getRoleHue, getRoleTooltipText } from "./rosterUtils";
import { Tooltip } from "../../primitives/Tooltip";
import "./roster.css";

const STATUS_LABELS: Record<RosterStatus, string> = {
  idle: "Idle",
  working: "Working",
  needs_you: "Needs Attention",
  unavailable: "Unavailable",
  invalid: "Invalid",
};

export const RosterRow: React.FC<RosterRowProps> = ({
  role,
  isSelected,
  isPinned,
  onSelect,
  onTogglePin,
  availableProviders,
  isFocused = false,
  isAutoRoute = false,
}) => {
  const { status, reason } = computeRoleStatus(role, availableProviders);
  const statusLabel = STATUS_LABELS[status];
  const tooltipDetail = getRoleTooltipText(role, availableProviders);
  const statusTooltip = reason ? `Status: ${statusLabel} (${tooltipDetail})` : `Status: ${statusLabel}`;

  const totalUnread = (role.caller_unread_count ?? 0) + (role.pending_proposals_count ?? 0);
  const relativeAge = formatRelativeTime(role.last_message_at);

  // role.holds (declared `holds:` guardrails, #1726) is informational only — it never
  // drives the status dot; only computeRoleStatus's status does.
  let statusDotType = "unavailable";
  if (status === "invalid") {
    statusDotType = "invalid";
  } else if (status === "working" || status === "needs_you") {
    statusDotType = "busy";
  } else if (status === "idle") {
    statusDotType = "available";
  } else {
    statusDotType = "unavailable";
  }

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    onSelect(role.name);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(role.name);
    }
  };

  const handlePinClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (onTogglePin) {
      onTogglePin(role.name);
    }
  };

  const avatarInitial = isAutoRoute
    ? "★"
    : (role.avatar || role.title.charAt(0) || role.name.charAt(0)).toUpperCase();

  const hue = getRoleHue(role.name);
  const avatarStyle: React.CSSProperties = isAutoRoute
    ? {}
    : {
        backgroundColor: `hsl(${hue}, 42%, 30%)`,
        color: "var(--text-on-accent)",
        borderColor: `hsl(${hue}, 48%, 42%)`,
      };

  return (
    <div
      data-testid={`roster-row-${role.name}`}
      data-selected={isSelected}
      data-focused={isFocused}
      data-status={status}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      className={`staff-roster-row ${isSelected ? "staff-roster-row--selected" : ""} ${
        isFocused ? "staff-roster-row--focused" : ""
      } ${isAutoRoute ? "staff-roster-row--barb" : ""}`}
    >
      {/* Accessible button for keyboard navigation & clicking */}
      <button
        type="button"
        data-testid={`roster-row-btn-${role.name}`}
        aria-label={`${role.title}, ${statusTooltip}`}
        onClick={handleClick}
        className="roster-row-btn"
      >
        {/* Avatar with status indicator */}
        <div className="roster-avatar-wrap">
          <div className="roster-avatar" style={avatarStyle}>
            {avatarInitial}
          </div>

          {/* Status dot with tooltip. The anchor pins it to the avatar corner;
              Tooltip's own relative wrapper would otherwise flow below. */}
          <span className="roster-status-dot-anchor">
            <Tooltip content={statusTooltip} placement="right" delayMs={150}>
              <span
                data-testid={`status-dot-${role.name}`}
                data-status={status}
                title={statusTooltip}
                aria-label={statusTooltip}
                className={`roster-status-dot roster-status-dot--${statusDotType}`}
              />
            </Tooltip>
          </span>
        </div>

        {/* Text Details */}
        <div className="roster-row-details">
          <div className="roster-row-name-wrap">
            <span className="roster-row-name">
              {role.title}
            </span>
          </div>

          {/* Subtitle / Mandate / Last Message Preview */}
          <div className="roster-row-mandate">
            {role.last_message_preview ? (
              <span>
                {role.last_message_preview}
                {relativeAge ? ` · ${relativeAge}` : ""}
              </span>
            ) : (
              <span>{role.summary || `@${role.name}`}</span>
            )}
          </div>
        </div>
      </button>

      {/* Right controls: unread badge + pin toggle */}
      <div className="roster-row-controls">
        {/* Hidden reason element preserving data-testid for assertions */}
        {(status === "unavailable" || status === "invalid") && reason && (
          <span
            data-testid={`status-reason-${role.name}`}
            className="sr-only"
            aria-hidden="true"
          >
            {reason}
          </span>
        )}

        {/* Unread badge */}
        {totalUnread > 0 && (
          <span
            data-testid={`unread-badge-${role.name}`}
            aria-label={`${totalUnread} unread messages`}
            className="roster-unread-badge"
          >
            {totalUnread}
          </span>
        )}

        {/* Pin toggle button with neutral SVG icon */}
        {onTogglePin && (
          <button
            type="button"
            data-testid={`pin-button-${role.name}`}
            aria-label={isPinned ? `Unpin ${role.title}` : `Pin ${role.title}`}
            title={isPinned ? "Unpin role" : "Pin role"}
            onClick={handlePinClick}
            className={`roster-pin-button ${isPinned ? "roster-pin-button--pinned" : ""}`}
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill={isPinned ? "currentColor" : "none"}
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="12" y1="17" x2="12" y2="22" />
              <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a1 1 0 0 0 1-1V3a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v2a1 1 0 0 0 1 1h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
};

