/**
 * FleetRowActions.tsx — Accessible row action menu for Machines and Runners.
 *
 * Implements SC-E6 (Issue #1333):
 * "Row menu on machines/runners: Bring online, Take offline, Restart, Compact disk, Diagnose"
 */

import React, { useEffect, useRef, useState } from "react";
import { FLEET_ACTION_KEYS, FLEET_ACTIONS, type FleetRowActionKey } from "./fleetActions";

export interface FleetRowActionsProps {
  target: string;
  isMachine?: boolean;
  onSelectAction: (key: FleetRowActionKey) => void;
  disabled?: boolean;
}

export const FleetRowActions: React.FC<FleetRowActionsProps> = ({
  target,
  isMachine = false,
  onSelectAction,
  disabled = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (ev: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(ev.target as Node)) {
        setIsOpen(false);
      }
    };

    const handleKeyDown = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") {
        setIsOpen(false);
        buttonRef.current?.focus();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  const handleSelect = (key: FleetRowActionKey) => {
    setIsOpen(false);
    onSelectAction(key);
  };

  return (
    <div
      ref={containerRef}
      style={{ position: "relative", display: "inline-block" }}
      data-testid={`fleet-row-actions-${target}`}
    >
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label={`Actions for ${isMachine ? "machine" : "runner"} ${target}`}
        onClick={() => setIsOpen((prev) => !prev)}
        className="btn"
        style={{
          fontSize: 11,
          padding: "2px 8px",
          background: "rgba(56, 139, 253, 0.12)",
          border: "1px solid var(--border-blue, #1f6feb)",
          color: "var(--accent-blue, #58a6ff)",
          cursor: disabled ? "not-allowed" : "pointer",
          borderRadius: 4,
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <span>Actions</span>
        <span style={{ fontSize: 9 }}>▼</span>
      </button>

      {isOpen && (
        <div
          role="menu"
          aria-label={`Maintenance actions for ${target}`}
          style={{
            position: "absolute",
            right: 0,
            top: "100%",
            marginTop: 4,
            zIndex: 100,
            background: "var(--bg-secondary, #161b22)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: 6,
            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.4)",
            minWidth: 150,
            padding: "4px 0",
          }}
        >
          {FLEET_ACTION_KEYS.map((key) => {
            const item = FLEET_ACTIONS[key];
            const isDanger = key === "take_offline";

            return (
              <button
                key={key}
                role="menuitem"
                type="button"
                onClick={() => handleSelect(key)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  background: "transparent",
                  border: "none",
                  padding: "6px 12px",
                  fontSize: 12,
                  cursor: "pointer",
                  color: isDanger
                    ? "var(--accent-red, #f85149)"
                    : "var(--text-primary, #c9d1d9)",
                  display: "block",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--bg-hover, rgba(177, 186, 196, 0.12))";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {item.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
