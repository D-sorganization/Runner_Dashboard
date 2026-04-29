export const colorTokens = {
  bgPrimary: "#0f1117",
  bgSecondary: "#161b22",
  bgTertiary: "#1c2333",
  bgCard: "#1c2128",
  bgHover: "#252d3a",
  border: "#30363d",
  borderLight: "#3d444d",
  textPrimary: "#e6edf3",
  textSecondary: "#8b949e",
  textMuted: "#6e7681",
  accentBlue: "#58a6ff",
  accentGreen: "#3fb950",
  accentRed: "#f85149",
  accentYellow: "#d29922",
  accentPurple: "#bc8cff",
  accentOrange: "#f0883e",
} as const;

export const surfaceTokens = {
  glassBg: "rgba(28, 33, 51, 0.7)",
  glassBorder: "rgba(255, 255, 255, 0.1)",
  glassBorderLight: "rgba(255, 255, 255, 0.05)",
  glassShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.37)",
  glassBlur: "blur(12px)",
} as const;

export const touchTokens = {
  minimumHitTarget: "44px",
  comfortableHitTarget: "48px",
  bottomNavHeight: "64px",
  safeAreaInsetBottom: "env(safe-area-inset-bottom)",
} as const;

export const cssVariableMap = {
  "--bg-primary": colorTokens.bgPrimary,
  "--bg-secondary": colorTokens.bgSecondary,
  "--bg-tertiary": colorTokens.bgTertiary,
  "--bg-card": colorTokens.bgCard,
  "--bg-hover": colorTokens.bgHover,
  "--border": colorTokens.border,
  "--border-light": colorTokens.borderLight,
  "--text-primary": colorTokens.textPrimary,
  "--text-secondary": colorTokens.textSecondary,
  "--text-muted": colorTokens.textMuted,
  "--accent-blue": colorTokens.accentBlue,
  "--accent-green": colorTokens.accentGreen,
  "--accent-red": colorTokens.accentRed,
  "--accent-yellow": colorTokens.accentYellow,
  "--accent-purple": colorTokens.accentPurple,
  "--accent-orange": colorTokens.accentOrange,
  "--mobile-hit-target": touchTokens.minimumHitTarget,
} as const;

export function toCssVariables(): string {
  return Object.entries(cssVariableMap)
    .map(([name, value]) => `${name}: ${value};`)
    .join("\n");
}
