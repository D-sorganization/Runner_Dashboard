// Design Tokens
export const darkColorTokens = {
  bgPrimary: "#0f1117",
  bgSecondary: "#161b22",
  bgTertiary: "#1c2333",
  bgCard: "#1c2128",
  bgHover: "#252d3a",
  border: "#30363d",
  borderLight: "#3d444d",
  textPrimary: "#e6edf3",
  textSecondary: "#8b949e",
  textMuted: "#7a838e",
  accentBlue: "#58a6ff",
  accentGreen: "#3fb950",
  accentRed: "#f85149",
  accentYellow: "#d29922",
  accentPurple: "#bc8cff",
  accentOrange: "#f0883e",
} as const;

export const lightColorTokens = {
  bgPrimary: "#ffffff",
  bgSecondary: "#f6f8fa",
  bgTertiary: "#f0f2f5",
  bgCard: "#ffffff",
  bgHover: "#f3f4f6",
  border: "#d0d7de",
  borderLight: "#e1e4e8",
  textPrimary: "#1f2328",
  textSecondary: "#656d76",
  textMuted: "#5c6570",
  accentBlue: "#0969da",
  accentGreen: "#1a7f37",
  accentRed: "#cf222e",
  accentYellow: "#9a6700",
  accentPurple: "#8250df",
  accentOrange: "#bc4c00",
} as const;

export const colorTokens = darkColorTokens;

export const darkBadgeTokens = {
  successBg: "rgba(63, 185, 80, 0.15)",
  successFg: "#3fb950",
  warningBg: "rgba(210, 153, 34, 0.15)",
  warningFg: "#d29922",
  dangerBg: "rgba(248, 81, 73, 0.15)",
  dangerFg: "#f85149",
  infoBg: "rgba(88, 166, 255, 0.15)",
  infoFg: "#58a6ff",
  neutralBg: "rgba(110, 118, 129, 0.15)",
  neutralFg: "#8b949e",
} as const;

export const lightBadgeTokens = {
  successBg: "rgba(26, 127, 55, 0.12)",
  successFg: "#1a7f37",
  warningBg: "rgba(154, 103, 0, 0.12)",
  warningFg: "#9a6700",
  dangerBg: "rgba(207, 34, 46, 0.12)",
  dangerFg: "#cf222e",
  infoBg: "rgba(9, 105, 218, 0.12)",
  infoFg: "#0969da",
  neutralBg: "rgba(101, 109, 118, 0.12)",
  neutralFg: "#656d76",
} as const;

export const badgeTokens = darkBadgeTokens;

export const darkSurfaceTokens = {
  glassBg: "rgba(28, 33, 51, 0.7)",
  glassBorder: "rgba(255, 255, 255, 0.1)",
  glassBorderLight: "rgba(255, 255, 255, 0.05)",
  glassShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.37)",
  glassBlur: "12px",
} as const;

export const lightSurfaceTokens = {
  glassBg: "rgba(255, 255, 255, 0.7)",
  glassBorder: "rgba(0, 0, 0, 0.08)",
  glassBorderLight: "rgba(0, 0, 0, 0.05)",
  glassShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.1)",
  glassBlur: "12px",
} as const;

export const surfaceTokens = darkSurfaceTokens;

export const spacingTokens = {
  0: "0px",
  1: "2px",
  2: "4px",
  3: "6px",
  4: "8px",
  5: "10px",
  6: "12px",
  7: "14px",
  8: "16px",
  9: "20px",
  10: "24px",
  11: "32px",
  12: "40px",
  13: "48px",
  14: "64px",
  15: "80px",
} as const;

export const touchTokens = {
  minimumHitTarget: "44px",
  comfortableHitTarget: "48px",
  bottomNavHeight: "64px",
  safeAreaInsetBottom: "env(safe-area-inset-bottom)",
} as const;

export const darkCssVariableMap = {
  "--bg-primary": darkColorTokens.bgPrimary,
  "--bg-secondary": darkColorTokens.bgSecondary,
  "--bg-tertiary": darkColorTokens.bgTertiary,
  "--bg-card": darkColorTokens.bgCard,
  "--bg-hover": darkColorTokens.bgHover,
  "--border": darkColorTokens.border,
  "--border-light": darkColorTokens.borderLight,
  "--text-primary": darkColorTokens.textPrimary,
  "--text-secondary": darkColorTokens.textSecondary,
  "--text-muted": darkColorTokens.textMuted,
  "--accent-blue": darkColorTokens.accentBlue,
  "--accent-green": darkColorTokens.accentGreen,
  "--accent-red": darkColorTokens.accentRed,
  "--accent-yellow": darkColorTokens.accentYellow,
  "--accent-purple": darkColorTokens.accentPurple,
  "--accent-orange": darkColorTokens.accentOrange,

  "--badge-success-bg": darkBadgeTokens.successBg,
  "--badge-success-fg": darkBadgeTokens.successFg,
  "--badge-warning-bg": darkBadgeTokens.warningBg,
  "--badge-warning-fg": darkBadgeTokens.warningFg,
  "--badge-danger-bg": darkBadgeTokens.dangerBg,
  "--badge-danger-fg": darkBadgeTokens.dangerFg,
  "--badge-info-bg": darkBadgeTokens.infoBg,
  "--badge-info-fg": darkBadgeTokens.infoFg,
  "--badge-neutral-bg": darkBadgeTokens.neutralBg,
  "--badge-neutral-fg": darkBadgeTokens.neutralFg,

  "--glass-bg": darkSurfaceTokens.glassBg,
  "--glass-border": darkSurfaceTokens.glassBorder,
  "--glass-border-light": darkSurfaceTokens.glassBorderLight,
  "--glass-shadow": darkSurfaceTokens.glassShadow,
  "--glass-blur": darkSurfaceTokens.glassBlur,

  "--mobile-hit-target": touchTokens.minimumHitTarget,
  "--comfortable-hit-target": touchTokens.comfortableHitTarget,
  "--bottom-nav-height": touchTokens.bottomNavHeight,

  "--space-0": spacingTokens[0],
  "--space-1": spacingTokens[1],
  "--space-2": spacingTokens[2],
  "--space-3": spacingTokens[3],
  "--space-4": spacingTokens[4],
  "--space-5": spacingTokens[5],
  "--space-6": spacingTokens[6],
  "--space-7": spacingTokens[7],
  "--space-8": spacingTokens[8],
  "--space-9": spacingTokens[9],
  "--space-10": spacingTokens[10],
  "--space-11": spacingTokens[11],
  "--space-12": spacingTokens[12],
  "--space-13": spacingTokens[13],
  "--space-14": spacingTokens[14],
  "--space-15": spacingTokens[15],
} as const;

export const lightCssVariableMap = {
  "--bg-primary": lightColorTokens.bgPrimary,
  "--bg-secondary": lightColorTokens.bgSecondary,
  "--bg-tertiary": lightColorTokens.bgTertiary,
  "--bg-card": lightColorTokens.bgCard,
  "--bg-hover": lightColorTokens.bgHover,
  "--border": lightColorTokens.border,
  "--border-light": lightColorTokens.borderLight,
  "--text-primary": lightColorTokens.textPrimary,
  "--text-secondary": lightColorTokens.textSecondary,
  "--text-muted": lightColorTokens.textMuted,
  "--accent-blue": lightColorTokens.accentBlue,
  "--accent-green": lightColorTokens.accentGreen,
  "--accent-red": lightColorTokens.accentRed,
  "--accent-yellow": lightColorTokens.accentYellow,
  "--accent-purple": lightColorTokens.accentPurple,
  "--accent-orange": lightColorTokens.accentOrange,

  "--badge-success-bg": lightBadgeTokens.successBg,
  "--badge-success-fg": lightBadgeTokens.successFg,
  "--badge-warning-bg": lightBadgeTokens.warningBg,
  "--badge-warning-fg": lightBadgeTokens.warningFg,
  "--badge-danger-bg": lightBadgeTokens.dangerBg,
  "--badge-danger-fg": lightBadgeTokens.dangerFg,
  "--badge-info-bg": lightBadgeTokens.infoBg,
  "--badge-info-fg": lightBadgeTokens.infoFg,
  "--badge-neutral-bg": lightBadgeTokens.neutralBg,
  "--badge-neutral-fg": lightBadgeTokens.neutralFg,

  "--glass-bg": lightSurfaceTokens.glassBg,
  "--glass-border": lightSurfaceTokens.glassBorder,
  "--glass-border-light": lightSurfaceTokens.glassBorderLight,
  "--glass-shadow": lightSurfaceTokens.glassShadow,
  "--glass-blur": lightSurfaceTokens.glassBlur,

  "--mobile-hit-target": touchTokens.minimumHitTarget,
  "--comfortable-hit-target": touchTokens.comfortableHitTarget,
  "--bottom-nav-height": touchTokens.bottomNavHeight,

  "--space-0": spacingTokens[0],
  "--space-1": spacingTokens[1],
  "--space-2": spacingTokens[2],
  "--space-3": spacingTokens[3],
  "--space-4": spacingTokens[4],
  "--space-5": spacingTokens[5],
  "--space-6": spacingTokens[6],
  "--space-7": spacingTokens[7],
  "--space-8": spacingTokens[8],
  "--space-9": spacingTokens[9],
  "--space-10": spacingTokens[10],
  "--space-11": spacingTokens[11],
  "--space-12": spacingTokens[12],
  "--space-13": spacingTokens[13],
  "--space-14": spacingTokens[14],
  "--space-15": spacingTokens[15],
} as const;

export const cssVariableMap = darkCssVariableMap;

export function toCssVariables(theme: "dark" | "light" = "dark"): string {
  const map = theme === "light" ? lightCssVariableMap : darkCssVariableMap;
  return Object.entries(map)
    .map(([name, value]) => `${name}: ${value};`)
    .join("\n");
}
