import React, { useCallback } from "react";
import { Dropdown, type DropdownItem } from "../primitives/Dropdown";
import { useThemeContext } from "../design/ThemeContext";
import {
  FLEET_THEMES,
  getFleetThemeDisplayName,
  type FleetThemeId,
} from "../design/fleetThemes";
import type { ThemeMode } from "../hooks/useTheme";

function ThemeIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="m4.93 4.93 1.41 1.41" />
      <path d="m17.66 17.66 1.41 1.41" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="m6.34 17.66-1.41 1.41" />
      <path d="m19.07 4.93-1.41 1.41" />
    </svg>
  );
}

export function ShellThemeControl(): React.ReactElement {
  const { mode, setMode } = useThemeContext();

  const handleSelect = useCallback(
    (next: ThemeMode) => {
      setMode(next);
    },
    [setMode],
  );

  const themeKeys = Object.keys(FLEET_THEMES) as FleetThemeId[];
  const items: DropdownItem[] = [
    {
      id: "system",
      label: "System",
      active: mode === "system",
      onSelect: () => handleSelect("system"),
    },
    ...themeKeys.map((themeId) => ({
      id: themeId,
      label: getFleetThemeDisplayName(themeId),
      active: mode === themeId,
      onSelect: () => handleSelect(themeId),
    })),
  ];

  const currentLabel =
    mode === "system" ? "System" : getFleetThemeDisplayName(mode);

  return (
    <div id="theme-selector" data-testid="theme-selector" style={{ display: "inline-flex" }}>
      <Dropdown
        id="theme-selector-toggle"
        label={currentLabel}
        tooltip={`Theme: ${currentLabel}`}
        Icon={ThemeIcon}
        hideLabel
        triggerClassName="shell-action shell-icon-btn"
        items={items}
        align="right"
      />
    </div>
  );
}
