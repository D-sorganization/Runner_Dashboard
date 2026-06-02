import React, { useMemo } from "react";
import { motionDurations, motionEasing, reducedMotionCss } from "./motion";
import { useTheme } from "../hooks/useTheme";
import { ThemeContext } from "./ThemeContext";

export interface ThemeProviderProps {
  children: React.ReactNode;
  reducedMotion?: boolean;
}

/**
 * ThemeProvider wires the single fleet-theme engine (`useTheme`) into context so
 * every consumer (ThemeSelector, ThemeSettings, legacy App) shares one source of
 * truth.
 *
 * Theme colour tokens for all 13 fleet themes (plus custom themes) are written
 * to `document.documentElement` by `useTheme` itself — this provider no longer
 * injects a competing `<style>` block of dark/light tokens, so CSS-variable
 * precedence is well-defined. The only `<style>` left here carries the static
 * motion tokens and the reduced-motion media query, which are theme-independent.
 */
export const ThemeProvider: React.FC<ThemeProviderProps> = ({
  children,
  reducedMotion = false,
}) => {
  const { theme, mode, setMode, accentColor, setAccentColor } = useTheme();

  const css = useMemo(() => {
    return `
      :root {
        --motion-instant: ${motionDurations.instant};
        --motion-fast: ${motionDurations.fast};
        --motion-normal: ${motionDurations.normal};
        --motion-slow: ${motionDurations.slow};
        --easing-standard: ${motionEasing.standard};
        --easing-emphasized: ${motionEasing.emphasized};
      }

      ${reducedMotion ? reducedMotionCss : ""}
    `;
  }, [reducedMotion]);

  return (
    <ThemeContext.Provider value={{ theme, mode, setMode, accentColor, setAccentColor }}>
      <style>{css}</style>
      {children}
    </ThemeContext.Provider>
  );
};
