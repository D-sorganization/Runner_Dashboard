import React, { useMemo } from "react";
import { toCssVariables } from "./tokens";
import { motionDurations, motionEasing, reducedMotionCss } from "./motion";
import { useTheme } from "../hooks/useTheme";
import { ThemeContext } from "./ThemeContext";
import {
  FLEET_THEMES,
  fleetThemeToCssVars,
  type FleetThemeId,
} from "./fleetThemes";

export interface ThemeProviderProps {
  children: React.ReactNode;
  reducedMotion?: boolean;
  fleetThemeId?: FleetThemeId;
}

function buildFleetVars(themeId: FleetThemeId): string {
  const def = FLEET_THEMES[themeId];
  if (!def) return "";
  const vars = fleetThemeToCssVars(def);
  return Object.entries(vars)
    .map(([key, val]) => `${key}: ${val};`)
    .join("\n        ");
}

/**
 * ThemeProvider injects the design-token CSS custom properties into a <style>
 * block so every component—legacy or modern—reads from the same source of truth.
 */
export const ThemeProvider: React.FC<ThemeProviderProps> = ({
  children,
  reducedMotion = false,
  fleetThemeId,
}) => {
  const { theme, mode, setMode, accentColor, setAccentColor } = useTheme();

  const css = useMemo(() => {
    // If a fleet theme is specified, it overrides the basic dark/light tokens
    const darkVars = fleetThemeId ? buildFleetVars(fleetThemeId) : toCssVariables("dark");
    const lightVars = toCssVariables("light");

    const customAccent = accentColor
      ? `
        --accent-blue: ${accentColor};
        --badge-info-fg: ${accentColor};
      `
      : "";

    return `
      :root {
        ${darkVars}
        --motion-instant: ${motionDurations.instant};
        --motion-fast: ${motionDurations.fast};
        --motion-normal: ${motionDurations.normal};
        --motion-slow: ${motionDurations.slow};
        --easing-standard: ${motionEasing.standard};
        --easing-emphasized: ${motionEasing.emphasized};
        ${theme === "dark" ? customAccent : ""}
      }

      [data-theme="light"] {
        ${lightVars}
        ${theme === "light" ? customAccent : ""}
      }

      ${reducedMotion ? reducedMotionCss : ""}
    `;
  }, [reducedMotion, theme, accentColor, fleetThemeId]);

  return (
    <ThemeContext.Provider value={{ theme, mode, setMode, accentColor, setAccentColor }}>
      <style>{css}</style>
      {children}
    </ThemeContext.Provider>
  );
};
