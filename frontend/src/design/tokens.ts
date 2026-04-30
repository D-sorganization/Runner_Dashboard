// Design Tokens — Single source of truth for the runner-dashboard visual system.
// These values are mirrored in frontend/src/index.css as CSS custom properties.

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

// Badge tints — paired background + foreground colours used by
// the <Badge /> primitive. Each tone has a low-alpha tinted background
// and a saturated foreground colour matching the same accent family.
export const badgeTokens = {
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

export const surfaceTokens = {
  glassBg: "rgba(28, 33, 51, 0.7)",
  glassBorder: "rgba(255, 255, 255, 0.1)",
  glassBorderLight: "rgba(255, 255, 255, 0.05)",
  glassShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.37)",
  // glassBlur is the blur RADIUS only — consumed in CSS via
  // `blur(var(--glass-blur))`. Override with `:root { --glass-blur: 0px }`
  // (or future in-app reduce-transparency toggle) to disable the effect.
  glassBlur: "12px",
} as const;

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

  "--badge-success-bg": badgeTokens.successBg,
  "--badge-success-fg": badgeTokens.successFg,
  "--badge-warning-bg": badgeTokens.warningBg,
  "--badge-warning-fg": badgeTokens.warningFg,
  "--badge-danger-bg": badgeTokens.dangerBg,
  "--badge-danger-fg": badgeTokens.dangerFg,
  "--badge-info-bg": badgeTokens.infoBg,
  "--badge-info-fg": badgeTokens.infoFg,
  "--badge-neutral-bg": badgeTokens.neutralBg,
  "--badge-neutral-fg": badgeTokens.neutralFg,

  "--glass-bg": surfaceTokens.glassBg,
  "--glass-border": surfaceTokens.glassBorder,
  "--glass-border-light": surfaceTokens.glassBorderLight,
  "--glass-shadow": surfaceTokens.glassShadow,
  "--glass-blur": surfaceTokens.glassBlur,

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

export function toCssVariables(): string {
  return Object.entries(cssVariableMap)
    .map(([name, value]) => `${name}: ${value};`)
    .join("\n");
}
