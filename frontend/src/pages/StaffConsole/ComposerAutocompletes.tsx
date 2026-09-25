/**
 * ComposerAutocompletes.tsx — Accessible popups for role @mentions and slash commands.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import React from "react";
import type { SlashCommand } from "./threadTypes";
import type { StaffRoleItem } from "./types";

export interface ComposerAutocompletesProps {
  activeMenu: "mention" | "slash" | null;
  matchedRoles: StaffRoleItem[];
  matchedCommands: SlashCommand[];
  selectedIndex: number;
  onSelectRole: (role: StaffRoleItem) => void;
  onSelectCommand: (cmd: SlashCommand) => void;
}

export const ComposerAutocompletes: React.FC<ComposerAutocompletesProps> = ({
  activeMenu,
  matchedRoles,
  matchedCommands,
  selectedIndex,
  onSelectRole,
  onSelectCommand,
}) => {
  if (activeMenu === "mention" && matchedRoles.length > 0) {
    return (
      <ul
        role="listbox"
        aria-label="Role mentions"
        style={{
          position: "absolute",
          bottom: "100%",
          left: 16,
          right: 16,
          marginBottom: 8,
          background: "var(--bg-overlay, #161b22)",
          border: "1px solid var(--border, #30363d)",
          borderRadius: 8,
          boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          maxHeight: 200,
          overflowY: "auto",
          listStyle: "none",
          padding: "4px 0",
          zIndex: 100,
        }}
      >
        {matchedRoles.map((role, idx) => (
          <li
            key={role.name}
            role="option"
            aria-selected={idx === selectedIndex}
            onClick={() => onSelectRole(role)}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 12px",
              cursor: "pointer",
              background: idx === selectedIndex ? "rgba(56, 139, 253, 0.15)" : "transparent",
              color: "var(--text-primary, #c9d1d9)",
              fontSize: 13,
            }}
          >
            <div>
              <strong>@{role.name}</strong>{" "}
              <span style={{ color: "var(--text-muted, #8b949e)", fontSize: 12 }}>
                {role.title}
              </span>
            </div>
            {role.summary && (
              <span style={{ color: "var(--text-muted, #8b949e)", fontSize: 11 }}>
                {role.summary.slice(0, 40)}
              </span>
            )}
          </li>
        ))}
      </ul>
    );
  }

  if (activeMenu === "slash" && matchedCommands.length > 0) {
    return (
      <ul
        role="listbox"
        aria-label="Slash commands"
        style={{
          position: "absolute",
          bottom: "100%",
          left: 16,
          right: 16,
          marginBottom: 8,
          background: "var(--bg-overlay, #161b22)",
          border: "1px solid var(--border, #30363d)",
          borderRadius: 8,
          boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          maxHeight: 200,
          overflowY: "auto",
          listStyle: "none",
          padding: "4px 0",
          zIndex: 100,
        }}
      >
        {matchedCommands.map((cmd, idx) => (
          <li
            key={cmd.name}
            role="option"
            aria-selected={idx === selectedIndex}
            onClick={() => onSelectCommand(cmd)}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 12px",
              cursor: "pointer",
              background: idx === selectedIndex ? "rgba(56, 139, 253, 0.15)" : "transparent",
              color: "var(--text-primary, #c9d1d9)",
              fontSize: 13,
            }}
          >
            <div>
              <strong style={{ color: "var(--accent-blue, #58a6ff)" }}>{cmd.label}</strong>{" "}
              <span style={{ color: "var(--text-muted, #8b949e)", fontSize: 12 }}>
                {cmd.description}
              </span>
            </div>
            <code style={{ fontSize: 11, color: "var(--text-muted, #8b949e)" }}>{cmd.syntax}</code>
          </li>
        ))}
      </ul>
    );
  }

  return null;
};
