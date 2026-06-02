import React from "react";
import { useThemeContext } from "../design/ThemeContext";
import { ThemeSelector } from "./ThemeSelector";

const PRESET_ACCENTS = [
  { name: "Blue", color: "#58a6ff" },
  { name: "Green", color: "#3fb950" },
  { name: "Red", color: "#f85149" },
  { name: "Yellow", color: "#d29922" },
  { name: "Purple", color: "#bc8cff" },
  { name: "Orange", color: "#f0883e" },
  { name: "Pink", color: "#ff7b72" },
  { name: "Teal", color: "#56d364" },
];

export function ThemeSettings() {
  const { mode, setMode, accentColor, setAccentColor } = useThemeContext();

  return (
    <div className="section" style={{ padding: "16px", marginBottom: "16px" }}>
      <h3 style={{ fontSize: "16px", marginBottom: "16px", fontWeight: 600 }}>Theme Settings</h3>
      
      <div style={{ marginBottom: "24px" }}>
        <h4 style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "8px", textTransform: "uppercase" }}>Appearance</h4>
        {/* All 13 fleet themes (+ System) are reachable here, not just the
            previous hardcoded system/light/dark trio. */}
        <ThemeSelector currentMode={mode} onThemeChange={setMode} />
      </div>

      <div>
        <h4 style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "8px", textTransform: "uppercase" }}>Accent Color</h4>
        <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
          <button
            onClick={() => setAccentColor(null)}
            style={{
              padding: "8px 16px",
              borderRadius: "6px",
              border: `1px solid ${!accentColor ? "var(--border-light)" : "var(--border)"}`,
              background: "var(--bg-card)",
              color: "var(--text-primary)",
              cursor: "pointer",
              fontWeight: 500
            }}
          >
            Default
          </button>
          {PRESET_ACCENTS.map((preset) => (
            <button
              key={preset.name}
              onClick={() => setAccentColor(preset.color)}
              title={preset.name}
              style={{
                width: "36px",
                height: "36px",
                borderRadius: "50%",
                border: `2px solid ${accentColor === preset.color ? "var(--text-primary)" : "transparent"}`,
                background: preset.color,
                cursor: "pointer",
                padding: 0,
                boxShadow: "0 2px 4px rgba(0,0,0,0.1)"
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
