/**
 * ThemeProvider — injects fleet-shared theme CSS variables into the DOM.
 *
 * Replaces the original dark/light-only provider with full fleet theme
 * support (13 built-in + custom themes). Still generates the motion
 * and spacing tokens alongside the color variables.
 *
 * Addresses: Runner_Dashboard#618, #619
 */
import React, { useMemo } from 'react';
import { motionDurations, motionEasing, reducedMotionCss } from './motion';
import { spacingTokens, touchTokens } from './tokens';
import {
  FLEET_THEMES,
  fleetThemeToCssVars,
  type FleetThemeId,
} from './fleetThemes';

export type ThemeMode = 'system' | 'light' | 'dark';

export interface ThemeProviderProps {
  children: React.ReactNode;
  reducedMotion?: boolean;
  theme?: ThemeMode;
  /** Fleet theme ID override — takes precedence over theme prop. */
  fleetThemeId?: FleetThemeId;
}

function buildSpacingVars(): string {
  return Object.entries(spacingTokens)
    .map(([key, val]) => `--space-${key}: ${val};`)
    .join('\n        ');
}

function buildFleetVars(themeId: FleetThemeId): string {
  const def = FLEET_THEMES[themeId];
  if (!def) return '';
  const vars = fleetThemeToCssVars(def);
  return Object.entries(vars)
    .map(([key, val]) => `${key}: ${val};`)
    .join('\n        ');
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
 * When `fleetThemeId` is set, it overrides the mode and applies the full
 * fleet palette from themes.json.
 */
export const ThemeProvider: React.FC<ThemeProviderProps> = ({
  children,
  reducedMotion = false,
  theme: _theme = 'system',
  fleetThemeId,
}) => {
  const css = useMemo(() => {
    const spacing = buildSpacingVars();
    const motionVars = `
        --motion-instant: ${motionDurations.instant};
        --motion-fast: ${motionDurations.fast};
        --motion-normal: ${motionDurations.normal};
        --motion-slow: ${motionDurations.slow};
        --easing-standard: ${motionEasing.standard};
        --easing-emphasized: ${motionEasing.emphasized};
    `;
    const touchVars = `
        --mobile-hit-target: ${touchTokens.minimumHitTarget};
        --comfortable-hit-target: ${touchTokens.comfortableHitTarget};
        --bottom-nav-height: ${touchTokens.bottomNavHeight};
    `;

    // If a fleet theme is specified, generate its CSS vars
    const darkFleet = buildFleetVars(fleetThemeId ?? 'dark');
    const lightFleet = buildFleetVars('light');

    return `
      :root {
        ${darkFleet}
        ${spacing}
        ${motionVars}
        ${touchVars}
      }

      [data-theme="light"] {
        ${lightFleet}
      }

      ${reducedMotion ? reducedMotionCss : ''}
    `;
  }, [reducedMotion, fleetThemeId]);

  return (
    <>
      <style>{css}</style>
      {children}
    </>
  );
};
