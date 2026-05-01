import React, { useMemo } from "react";
import { toCssVariables } from "./tokens";
import { motionDurations, motionEasing, reducedMotionCss } from "./motion";

export type ThemeMode = "system" | "light" | "dark";

export interface ThemeProviderProps {
  children: React.ReactNode;
  reducedMotion?: boolean;
  theme?: ThemeMode;
}

/**
 * ThemeProvider injects the design-token CSS custom properties into a <style>
 * block so every component—legacy or modern—reads from the same source of truth.
 *
 * It supports three modes:
 *   - "system": follows prefers-color-scheme
 *   - "light":  forces light theme
 *   - "dark":   forces dark theme
 *
 * Theme transitions are instant (0ms) to satisfy prefers-reduced-motion.
 */
export const ThemeProvider: React.FC<ThemeProviderProps> = ({
  children,
  reducedMotion = false,
  theme = "system",
}) => {
  const css = useMemo(() => {
    const darkVars = toCssVariables("dark");
    const lightVars = toCssVariables("light");

    return `
      :root {
        ${darkVars}
        --motion-instant: ${motionDurations.instant};
        --motion-fast: ${motionDurations.fast};
        --motion-normal: ${motionDurations.normal};
        --motion-slow: ${motionDurations.slow};
        --easing-standard: ${motionEasing.standard};
        --easing-emphasized: ${motionEasing.emphasized};
      }

      [data-theme="light"] {
        ${lightVars}
      }

      ${reducedMotion ? reducedMotionCss : ""}
    `;
  }, []);

  return (
    <>
      <style>{css}</style>
      {children}
    </>
  );
};
