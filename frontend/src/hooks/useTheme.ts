import { useState, useEffect, useCallback } from "react";

export type ThemeMode = "system" | "light" | "dark";

const STORAGE_KEY = "runner-dashboard:theme-mode";

function getSystemTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getStoredMode(): ThemeMode {
  if (typeof window === "undefined") return "system";
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    // ignore
  }
  return "system";
}

/**
 * useTheme provides the current resolved theme, the user's chosen mode,
 * and a setter that persists to localStorage.
 */
export function useTheme() {
  const [mode, setModeState] = useState<ThemeMode>(() => getStoredMode());
  const [theme, setThemeState] = useState<"light" | "dark">(() => {
    const stored = getStoredMode();
    return stored === "system" ? getSystemTheme() : stored;
  });

  useEffect(() => {
    const resolved = mode === "system" ? getSystemTheme() : mode;
    setThemeState(resolved);
    document.documentElement.setAttribute("data-theme", resolved);
  }, [mode]);

  useEffect(() => {
    if (mode !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e: MediaQueryListEvent) => {
      const resolved = e.matches ? "dark" : "light";
      setThemeState(resolved);
      document.documentElement.setAttribute("data-theme", resolved);
    };
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore
    }
  }, []);

  return { theme, mode, setMode };
}
