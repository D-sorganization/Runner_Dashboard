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

/**
 * Apply fleet theme CSS variables to the document root.
 */
function applyFleetCssVars(vars: Record<string, string>, isDark: boolean): void {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
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

  // Apply CSS variables when theme changes
  useEffect(() => {
    applyFleetCssVars(cssVars, isDark);
  }, [cssVars, isDark]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
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
    /** Save a custom theme. */
    saveCustomTheme,
    /** Delete a custom theme. */
    deleteCustomTheme,
  };
}
