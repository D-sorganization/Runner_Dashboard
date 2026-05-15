import { createContext, useContext } from "react";
import type { ThemeMode } from "../hooks/useTheme";

export interface ThemeContextValue {
  theme: "light" | "dark";
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  accentColor: string | null;
  setAccentColor: (color: string | null) => void;
}

export const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  mode: "system",
  setMode: () => {},
  accentColor: null,
  setAccentColor: () => {},
});

export const useThemeContext = () => useContext(ThemeContext);
