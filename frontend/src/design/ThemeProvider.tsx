import React, { useMemo } from "react";
import { toCssVariables } from "./tokens";
import { motionDurations, motionEasing, reducedMotionCss } from "./motion";
import { useTheme } from "../hooks/useTheme";
import { ThemeContext } from "./ThemeContext";

export interface ThemeProviderProps {
  children: React.ReactNode;
  reducedMotion?: boolean;
}

/**
 * ThemeProvider injects the design-token CSS custom properties into a <style>
 * block so every component—legacy or modern—reads from the same source of truth.
 *
 * It supports three modes via useTheme context:
 *   - "system": follows prefers-color-scheme
 *   - "light":  forces light theme
 *   - "dark":   forces dark theme
 *
 * Theme transitions are instant (0ms) to satisfy prefers-reduced-motion.
 */
export const ThemeProvider: React.FC<ThemeProviderProps> = ({
  children,
  reducedMotion = false,
}) => {
  const { theme, mode, setMode, accentColor, setAccentColor } = useTheme();

  const css = useMemo(() => {
    const darkVars = toCssVariables("dark");
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
  }, [reducedMotion, theme, accentColor]);

  return (
    <ThemeContext.Provider value={{ theme, mode, setMode, accentColor, setAccentColor }}>
      <style>{css}</style>
      {children}
    </ThemeContext.Provider>
  );
};
