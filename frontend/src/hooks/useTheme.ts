/**
 * useTheme — React hook for fleet-wide theme management.
 *
 * Extends the original system/light/dark toggle to support all 13
 * fleet themes from the canonical themes.json, plus custom user themes.
 * Maintains backward compatibility: 'system', 'light', 'dark' still work.
 *
 * Addresses: Runner_Dashboard#618 (Color Theme Management)
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FLEET_THEMES,
  fleetThemeToCssVars,
  getFleetThemeIds,
  isFleetThemeDark,
  type FleetThemeId,
} from '../design/fleetThemes';

export type ThemeMode = 'system' | FleetThemeId;

const STORAGE_KEY = 'runner-dashboard:theme-mode';
const ACCENT_STORAGE_KEY = 'runner-dashboard:accent-color';
const CUSTOM_THEMES_KEY = 'runner-dashboard:custom-themes';

/** Custom theme stored in localStorage. */
export interface CustomTheme {
  id: string;
  name: string;
  isDark: boolean;
  cssVars: Record<string, string>;
  createdAt: string;
}

function getSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getStoredMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system';
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === 'system') return 'system';
    if (raw && raw in FLEET_THEMES) return raw as FleetThemeId;
  } catch {
    // ignore
  }
  return 'system';
}

function loadCustomThemes(): CustomTheme[] {
  try {
    const raw = localStorage.getItem(CUSTOM_THEMES_KEY);
    if (raw) return JSON.parse(raw) as CustomTheme[];
  } catch {
    // corrupt data
  }
  return [];
}

function saveCustomThemesToStorage(themes: CustomTheme[]): void {
  try {
    localStorage.setItem(CUSTOM_THEMES_KEY, JSON.stringify(themes));
  } catch {
    // storage full
  }
}

function getStoredAccent(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(ACCENT_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** CSS variables overridden by a custom accent color. */
const ACCENT_VAR_KEYS = ['--accent-blue', '--badge-info-fg'] as const;

/**
 * Apply fleet theme CSS variables to the document root, layering an optional
 * custom accent color on top so accent overrides survive every theme switch.
 *
 * Single source of truth: this is the ONLY place CSS custom properties are
 * written to the document. ThemeProvider no longer injects a competing
 * <style> block, so precedence is well-defined.
 */
function applyFleetCssVars(
  vars: Record<string, string>,
  isDark: boolean,
  accentColor: string | null,
): void {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
  // Accent override layers on top of the resolved theme. Clearing it falls
  // back to the theme's own accent (set in the loop above).
  for (const key of ACCENT_VAR_KEYS) {
    if (accentColor) {
      root.style.setProperty(key, accentColor);
    }
  }
  root.setAttribute('data-theme', isDark ? 'dark' : 'light');
}

/**
 * useTheme provides fleet-wide theme management with:
 * - All 13 built-in fleet themes
 * - System preference auto-detection
 * - Custom theme CRUD with localStorage persistence
 * - CSS variable injection to document root
 */
export function useTheme() {
  const [mode, setModeState] = useState<ThemeMode>(() => getStoredMode());
  const [systemTheme, setSystemTheme] = useState<'light' | 'dark'>(() => getSystemTheme());
  const [customThemes, setCustomThemes] = useState<CustomTheme[]>(loadCustomThemes);
  const [accentColor, setAccentColorState] = useState<string | null>(() => getStoredAccent());

  // Resolved fleet theme ID
  const resolvedThemeId: FleetThemeId = useMemo(() => {
    if (mode === 'system') return systemTheme;
    return mode;
  }, [mode, systemTheme]);

  // Backward-compat: resolved 'light' | 'dark'
  const theme = useMemo<'light' | 'dark'>(() => {
    return isFleetThemeDark(resolvedThemeId) ? 'dark' : 'light';
  }, [resolvedThemeId]);

  const isDark = theme === 'dark';

  // CSS variables for current theme
  const cssVars = useMemo(() => {
    const custom = customThemes.find((t) => t.id === resolvedThemeId);
    if (custom) return custom.cssVars;
    const fleet = FLEET_THEMES[resolvedThemeId];
    if (!fleet) return fleetThemeToCssVars(FLEET_THEMES.dark);
    return fleetThemeToCssVars(fleet);
  }, [resolvedThemeId, customThemes]);

  // Listen for system preference changes
  useEffect(() => {
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      setSystemTheme(e.matches ? 'dark' : 'light');
    };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  // Apply CSS variables when theme or accent changes. The accent override is
  // re-layered on every theme switch so it never gets clobbered by a new theme.
  useEffect(() => {
    applyFleetCssVars(cssVars, isDark, accentColor);
  }, [cssVars, isDark, accentColor]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore
    }
  }, []);

  const setAccentColor = useCallback((color: string | null) => {
    setAccentColorState(color);
    try {
      if (color) {
        localStorage.setItem(ACCENT_STORAGE_KEY, color);
      } else {
        localStorage.removeItem(ACCENT_STORAGE_KEY);
      }
    } catch {
      // ignore
    }
  }, []);

  const saveCustomTheme = useCallback(
    (ct: CustomTheme) => {
      setCustomThemes((prev) => {
        const filtered = prev.filter((t) => t.id !== ct.id);
        const updated = [...filtered, ct];
        saveCustomThemesToStorage(updated);
        return updated;
      });
    },
    [],
  );

  const deleteCustomTheme = useCallback(
    (themeId: string) => {
      setCustomThemes((prev) => {
        const updated = prev.filter((t) => t.id !== themeId);
        saveCustomThemesToStorage(updated);
        return updated;
      });
    },
    [],
  );

  return {
    /** Resolved light/dark for backward compat. */
    theme,
    /** User's raw preference (may be 'system' or a fleet theme ID). */
    mode,
    /** Resolved fleet theme ID. */
    resolvedThemeId,
    /** Whether current theme is dark. */
    isDark,
    /** All available fleet theme IDs. */
    availableThemes: getFleetThemeIds(),
    /** User-saved custom themes. */
    customThemes,
    /** Change theme mode. */
    setMode,
    /** Current custom accent color. */
    accentColor,
    /** Set custom accent color. */
    setAccentColor,
    /** Save a custom theme. */
    saveCustomTheme,
    /** Delete a custom theme. */
    deleteCustomTheme,
  };
}
