export interface AccentPreset {
  name: string;
  value: string;
  tone: string;
}

export const ACCENT_PRESETS: AccentPreset[] = [
  { name: "Blue", value: "#58a6ff", tone: "blue" },
  { name: "Green", value: "#3fb950", tone: "green" },
  { name: "Red", value: "#f85149", tone: "red" },
  { name: "Yellow", value: "#d29922", tone: "yellow" },
  { name: "Purple", value: "#bc8cff", tone: "purple" },
  { name: "Orange", value: "#f0883e", tone: "orange" },
  { name: "Pink", value: "#ff7b72", tone: "pink" },
  { name: "Teal", value: "#56d364", tone: "teal" },
];
