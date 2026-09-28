// Design Tokens
export const darkColorTokens = {
  bgPrimary: "#111113",
  bgSecondary: "#18181b",
  bgTertiary: "#1f1f23",
  bgCard: "#18181b",
  bgHover: "#26262b",
  border: "#2a2a30",
  borderLight: "#34343b",
  textPrimary: "#ececef",
  textSecondary: "#a1a1aa",
  textMuted: "#8a8a93",
  accentBlue: "#58a6ff",
  accentGreen: "#3fb950",
  accentRed: "#f85149",
  accentYellow: "#d29922",
  accentPurple: "#bc8cff",
  accentOrange: "#f0883e",
} as const;

export const lightColorTokens = {
  bgPrimary: "#ffffff",
  bgSecondary: "#fafafa",
  bgTertiary: "#f4f4f5",
  bgCard: "#ffffff",
  bgHover: "#f4f4f5",
  border: "#e4e4e7",
  borderLight: "#ececef",
  textPrimary: "#18181b",
  textSecondary: "#52525b",
  textMuted: "#6b6b74",
  accentBlue: "#0969da",
  accentGreen: "#1a7f37",
  accentRed: "#cf222e",
  accentYellow: "#9a6700",
  accentPurple: "#8250df",
  accentOrange: "#bc4c00",
} as const;

export const colorTokens = darkColorTokens;

export const darkBadgeTokens = {
  successBg: "rgba(66, 168, 95, 0.15)",
  successFg: "#42a85f",
  warningBg: "rgba(226, 179, 64, 0.15)",
  warningFg: "#e2b340",
  dangerBg: "rgba(248, 113, 113, 0.15)",
  dangerFg: "#f87171",
  infoBg: "rgba(96, 165, 250, 0.15)",
  infoFg: "#60a5fa",
  neutralBg: "rgba(161, 161, 170, 0.15)",
  neutralFg: "#a1a1aa",
} as const;

export const lightBadgeTokens = {
  successBg: "rgba(27, 114, 47, 0.12)",
  successFg: "#1b722f",
  warningBg: "rgba(127, 95, 0, 0.12)",
  warningFg: "#7f5f00",
  dangerBg: "rgba(184, 29, 44, 0.12)",
  dangerFg: "#b81d2c",
  infoBg: "rgba(16, 110, 125, 0.12)",
  infoFg: "#106e7d",
  neutralBg: "rgba(82, 82, 91, 0.12)",
  neutralFg: "#52525b",
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

export const fontSizeTokens = {
  xs: "12px",
  sm: "13px",
  md: "14px",
  base: "14px",
  prose: "15px",
  lg: "17px",
  xl: "20px",
  "2xl": "24px",
} as const;

export const semanticTokens = {
  dark: {
    surfaceRaised: "#1f1f23",
    focusRing: "#58a6ff",
    textProse: "15px / 1.65",
  },
  light: {
    surfaceRaised: "#ffffff",
    focusRing: "#0969da",
    textProse: "15px / 1.65",
  },
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

  "--surface-raised": semanticTokens.dark.surfaceRaised,
  "--focus-ring": semanticTokens.dark.focusRing,
  "--text-prose": semanticTokens.dark.textProse,

  "--font-size-xs": fontSizeTokens.xs,
  "--font-size-sm": fontSizeTokens.sm,
  "--font-size-md": fontSizeTokens.md,
  "--font-size-base": fontSizeTokens.base,
  "--font-size-prose": fontSizeTokens.prose,
  "--font-size-lg": fontSizeTokens.lg,
  "--font-size-xl": fontSizeTokens.xl,
  "--font-size-2xl": fontSizeTokens["2xl"],

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

  "--surface-raised": semanticTokens.light.surfaceRaised,
  "--focus-ring": semanticTokens.light.focusRing,
  "--text-prose": semanticTokens.light.textProse,

  "--font-size-xs": fontSizeTokens.xs,
  "--font-size-sm": fontSizeTokens.sm,
  "--font-size-md": fontSizeTokens.md,
  "--font-size-base": fontSizeTokens.base,
  "--font-size-prose": fontSizeTokens.prose,
  "--font-size-lg": fontSizeTokens.lg,
  "--font-size-xl": fontSizeTokens.xl,
  "--font-size-2xl": fontSizeTokens["2xl"],

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

// ---- Semantic status tokens (D5 / issue #724) --------------------------------

export const statusTokens = {
  healthy:  { bg: "rgba(66, 168, 95, 0.15)",   fg: "#42a85f" },
  warning:  { bg: "rgba(226, 179, 64, 0.15)",  fg: "#e2b340" },
  critical: { bg: "rgba(248, 113, 113, 0.15)", fg: "#f87171" },
  unknown:  { bg: "rgba(161, 161, 170, 0.15)", fg: "#a1a1aa" },
  info:     { bg: "rgba(96, 165, 250, 0.15)",  fg: "#60a5fa" },
} as const;

export type StatusVariant = keyof typeof statusTokens;

export const radii = {
  sm:   "6px",
  md:   "10px",
  lg:   "14px",
  pill: "9999px",
} as const;

export const shadows = {
  soft:  "0 1px 3px rgba(0,0,0,0.28), 0 1px 2px rgba(0,0,0,0.22)",
  card:  "0 4px 6px rgba(0,0,0,0.32), 0 1px 3px rgba(0,0,0,0.24)",
  modal: "0 20px 60px rgba(0,0,0,0.5), 0 8px 24px rgba(0,0,0,0.38)",
} as const;

// ---- Elevation system: radius/shadow/status → CSS vars (issue #827) ----------
// These maps make the radii/shadows/statusTokens above first-class CSS custom
// properties so components stop hardcoding 6/8/10/12/14px radii and ad-hoc
// rgba(0,0,0,.x) shadows. The mirror of these values lives in index.css :root;
// this module is the typed single source of truth (DRY).

export const radiiCssVars = {
  "--radius-sm": radii.sm,
  "--radius-md": radii.md,
  "--radius-lg": radii.lg,
  "--radius-pill": radii.pill,
} as const;

export const shadowsCssVars = {
  "--shadow-soft": shadows.soft,
  "--shadow-card": shadows.card,
  "--shadow-modal": shadows.modal,
} as const;

export const statusCssVars = {
  "--status-healthy-bg": statusTokens.healthy.bg,
  "--status-healthy-fg": statusTokens.healthy.fg,
  "--status-warning-bg": statusTokens.warning.bg,
  "--status-warning-fg": statusTokens.warning.fg,
  "--status-critical-bg": statusTokens.critical.bg,
  "--status-critical-fg": statusTokens.critical.fg,
  "--status-unknown-bg": statusTokens.unknown.bg,
  "--status-unknown-fg": statusTokens.unknown.fg,
  "--status-info-bg": statusTokens.info.bg,
  "--status-info-fg": statusTokens.info.fg,
} as const;

/** Modular type scale (issue #828): 11/12/14/16/20/26/34 + display family. */
export const typeScaleCssVars = {
  "--font-micro": "11px",
  "--font-meta": "12px",
  "--font-body": "14px",
  "--font-section-title": "16px",
  "--font-title": "20px",
  "--font-headline": "26px",
  "--font-display": "34px",
} as const;

/**
 * Theme-neutral elevation/type CSS variables. These are layered into the
 * document root once (radius rhythm, shadow scale, status tints, type scale
 * are independent of the active colour theme — per-theme shadow tuning is done
 * via the `[data-theme="light"]` selector in index.css).
 */
export const elevationCssVars = {
  ...radiiCssVars,
  ...shadowsCssVars,
  ...statusCssVars,
  ...typeScaleCssVars,
} as const;

/** Serialise the theme-neutral elevation/type vars to a CSS declaration block. */
export function elevationToCssVariables(): string {
  return Object.entries(elevationCssVars)
    .map(([name, value]) => `${name}: ${value};`)
    .join("\n");
}
