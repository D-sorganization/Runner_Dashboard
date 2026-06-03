import React from "react";
import { ACCENT_PRESETS } from "../design/accentPresets";
import { useThemeContext } from "../design/ThemeContext";
import { TouchButton } from "../primitives/TouchButton";
import { ThemeSelector } from "./ThemeSelector";

export function ThemeSettings() {
  const { mode, setMode, accentColor, setAccentColor } = useThemeContext();

  return (
    <div className="section theme-settings">
      <h3 className="theme-settings__title">Theme Settings</h3>
      
      <div className="theme-settings__group">
        <h4 className="theme-settings__group-title">Appearance</h4>
        {/* All 13 fleet themes (+ System) are reachable here, not just the
            previous hardcoded system/light/dark trio. */}
        <ThemeSelector currentMode={mode} onThemeChange={setMode} />
      </div>

      <div>
        <h4 className="theme-settings__group-title">Accent Color</h4>
        <div className="theme-settings__accent-list">
          <TouchButton
            className="theme-settings__default-accent"
            onClick={() => setAccentColor(null)}
            pressed={!accentColor}
          >
            Default
          </TouchButton>
          {ACCENT_PRESETS.map((preset) => (
            <button
              key={preset.name}
              onClick={() => setAccentColor(preset.value)}
              title={preset.name}
              aria-label={`${preset.name} accent`}
              aria-pressed={accentColor === preset.value}
              className={`theme-settings__accent-swatch theme-settings__accent-swatch--${preset.tone}`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
