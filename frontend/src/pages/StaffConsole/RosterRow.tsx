/**
 * RosterRow.tsx — Individual role entry in the Staff Console Roster sidebar.
 *
 * Implements SC-D3 (Issue #1317) under Epic SC-D (#1350).
 */
import React from "react";
import type { RosterRowProps, RosterStatus } from "./types";
import { computeRoleStatus, formatRelativeTime } from "./rosterUtils";

const STATUS_COLORS: Record<RosterStatus, { bg: string; border: string; label: string }> = {
  idle: {
    bg: "var(--accent-teal, #3fb950)",
    border: "rgba(63, 185, 80, 0.4)",
    label: "Idle",
  },
  working: {
    bg: "var(--accent-blue, #58a6ff)",
    border: "rgba(88, 166, 255, 0.4)",
    label: "Working",
  },
  needs_you: {
    bg: "var(--accent-yellow, #d29922)",
    border: "rgba(210, 153, 34, 0.4)",
    label: "Needs Attention",
  },
  unavailable: {
    bg: "var(--text-muted, #868e98)",
    border: "rgba(134, 142, 152, 0.4)",
    label: "Unavailable",
  },
  invalid: {
    bg: "var(--accent-red, #f85149)",
    border: "rgba(248, 81, 73, 0.4)",
    label: "Invalid",
  },
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
  const statusMeta = STATUS_COLORS[status];
  const statusTooltip = reason ? `${statusMeta.label}: ${reason}` : statusMeta.label;

  const totalUnread = (role.caller_unread_count ?? 0) + (role.pending_proposals_count ?? 0);
  const relativeAge = formatRelativeTime(role.last_message_at);

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

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid={`roster-row-${role.name}`}
      data-selected={isSelected}
      data-focused={isFocused}
      data-status={status}
      aria-selected={isSelected}
      aria-label={`${role.title}, status: ${statusTooltip}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 12px",
        margin: "2px 0",
        borderRadius: "6px",
        cursor: "pointer",
        backgroundColor: isSelected
          ? "var(--bg-hover, #252d3a)"
          : isFocused
          ? "var(--bg-tertiary, #1c2333)"
          : "transparent",
        border: isSelected
          ? "1px solid var(--accent-blue, #58a6ff)"
          : isFocused
          ? "1px solid var(--border-light, #3d444d)"
          : "1px solid transparent",
        transition: "background-color 0.15s ease, border-color 0.15s ease",
        userSelect: "none",
        outline: "none",
      }}
    >
      {/* Left section: Avatar + Role Info */}
      <div style={{ display: "flex", alignItems: "center", minWidth: 0, flex: 1, gap: "10px" }}>
        {/* Avatar with status indicator */}
        <div style={{ position: "relative", flexShrink: 0 }}>
          <div
            style={{
              width: "32px",
              height: "32px",
              borderRadius: "50%",
              backgroundColor: isAutoRoute
                ? "var(--accent-purple, #bc8cff)"
                : "var(--bg-card, #1c2128)",
              border: "1px solid var(--border, #30363d)",
              color: isAutoRoute ? "#ffffff" : "var(--text-primary, #e6edf3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 600,
              fontSize: isAutoRoute ? "16px" : "13px",
            }}
          >
            {avatarInitial}
          </div>

          {/* Status Dot */}
          <span
            data-testid={`status-dot-${role.name}`}
            data-status={status}
            title={statusTooltip}
            aria-label={statusTooltip}
            style={{
              position: "absolute",
              bottom: "-1px",
              right: "-1px",
              width: "10px",
              height: "10px",
              borderRadius: "50%",
              backgroundColor: statusMeta.bg,
              border: `2px solid var(--bg-primary, #0f1117)`,
              boxShadow: `0 0 0 1px ${statusMeta.border}`,
            }}
          />
        </div>

        {/* Text Details */}
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span
              style={{
                fontWeight: isSelected ? 600 : 500,
                fontSize: "13px",
                color: isSelected
                  ? "var(--accent-blue, #58a6ff)"
                  : "var(--text-primary, #e6edf3)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {role.title}
            </span>
          </div>

          {/* Subtitle / Last Message Preview */}
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-secondary, #8b949e)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              marginTop: "1px",
            }}
          >
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
      </div>

      {/* Right section: Unread badge + Pin toggle */}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: 0 }}>
        {/* Tooltip trigger or reason indicator if unavailable/invalid */}
        {(status === "unavailable" || status === "invalid") && reason && (
          <span
            data-testid={`status-reason-${role.name}`}
            title={statusTooltip}
            style={{
              fontSize: "10px",
              padding: "2px 5px",
              borderRadius: "4px",
              backgroundColor:
                status === "invalid"
                  ? "var(--badge-danger-bg, rgba(248, 81, 73, 0.15))"
                  : "var(--badge-neutral-bg, rgba(110, 118, 129, 0.15))",
              color:
                status === "invalid"
                  ? "var(--badge-danger-fg, #f85149)"
                  : "var(--badge-neutral-fg, #8b949e)",
              maxWidth: "85px",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {reason}
          </span>
        )}

        {/* Unread badge */}
        {totalUnread > 0 && (
          <span
            data-testid={`unread-badge-${role.name}`}
            aria-label={`${totalUnread} unread messages`}
            style={{
              backgroundColor: "var(--accent-blue, #58a6ff)",
              color: "var(--text-on-accent, #ffffff)",
              fontSize: "11px",
              fontWeight: 700,
              padding: "1px 6px",
              borderRadius: "10px",
              minWidth: "18px",
              textAlign: "center",
            }}
          >
            {totalUnread}
          </span>
        )}

        {/* Pin toggle button */}
        {onTogglePin && (
          <button
            type="button"
            data-testid={`pin-button-${role.name}`}
            aria-label={isPinned ? `Unpin ${role.title}` : `Pin ${role.title}`}
            title={isPinned ? "Unpin role" : "Pin role"}
            onClick={handlePinClick}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "4px",
              color: isPinned
                ? "var(--accent-yellow, #d29922)"
                : "var(--text-muted, #868e98)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "14px",
              borderRadius: "4px",
            }}
          >
            {isPinned ? "📌" : "📍"}
          </button>
        )}
      </div>
    </div>
  );
};
