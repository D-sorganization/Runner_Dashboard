/**
 * Legacy and component-level CSS variable aliases (issue #3174).
 *
 * Many pages still reference GitHub-style names (--border-color, --text-link,
 * --badge-success-text, …). These map onto the canonical design tokens and
 * fleet semantic colours so light mode receives concrete values when
 * `useTheme` writes inline vars on <html>.
 */
import {
  darkBadgeTokens,
  darkColorTokens,
  lightBadgeTokens,
  lightColorTokens,
  shadows,
} from './tokens';
import type { FleetThemeDef } from './fleetThemes';

const MONO_STACK =
  'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace';
const BODY_FAMILY =
  "'Inter', -apple-system, BlinkMacSystemFont, \"Segoe UI\", Helvetica, Arial, sans-serif";

type AliasInputs = {
  isDark: boolean;
  bg: string;
  groupBg: string;
  border: string;
  text: string;
  textSecondary: string;
  label: string;
  inputBg: string;
  link: string;
  success: string;
  warning: string;
  error: string;
  selectionText: string;
  accentOrange: string;
};

function subtleBorder(isDark: boolean): string {
  return isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)';
}

function semanticAliasVarsFromInputs(i: AliasInputs): Record<string, string> {
  const borderMuted = i.isDark ? '#6e7681' : '#8c959f';
  const dangerSubtle = i.isDark ? 'rgba(248, 81, 73, 0.1)' : 'rgba(207, 34, 46, 0.08)';
  const warningSubtle = i.isDark ? 'rgba(210, 153, 34, 0.15)' : 'rgba(154, 103, 0, 0.12)';
  const activeBg = `${i.link}33`;

  return {
    '--fg': i.text,
    '--fg-muted': i.label,
    '--text-link': i.link,
    '--text-accent': i.link,
    '--text-danger': i.error,
    '--text-warning': i.warning,
    '--text-tertiary': i.textSecondary,
    '--border-color': i.border,
    '--border-subtle': subtleBorder(i.isDark),
    '--border-muted': borderMuted,
    '--border-active': i.link,
    '--border-blue': i.link,
    '--border-danger': i.error,
    '--border-warning': i.warning,
    '--border-green': i.success,
    '--border-red': i.error,
    '--border-yellow': i.warning,
    '--card-bg': i.groupBg,
    '--bg-input': i.inputBg,
    '--bg-overlay': i.groupBg,
    '--bg-active': activeBg,
    '--bg-danger-subtle': dangerSubtle,
    '--bg-warning-subtle': warningSubtle,
    '--accent-amber': i.accentOrange,
    '--accent-red-bg': i.isDark ? darkBadgeTokens.dangerBg : lightBadgeTokens.dangerBg,
    '--badge-success-text': i.success,
    '--badge-warning-text': i.warning,
    '--badge-danger-text': i.error,
    '--badge-neutral-text': i.textSecondary,
    '--btn-primary-bg': i.success,
    '--btn-primary-text': i.selectionText,
    '--color-fg-on-emphasis': i.selectionText,
    '--color-text-secondary': i.textSecondary,
    '--status-danger': i.error,
    '--shadow-sm': i.isDark ? shadows.soft : '0 1px 2px rgba(16, 24, 40, 0.06), 0 1px 3px rgba(16, 24, 40, 0.10)',
    '--font-mono': MONO_STACK,
    '--font-mono-family': MONO_STACK,
    '--font-body-family': BODY_FAMILY,
  };
}

/** Semantic / legacy aliases for a fleet theme (injected by useTheme). */
export function semanticAliasVarsForFleetTheme(theme: FleetThemeDef): Record<string, string> {
  const c = theme.colors;
  const s = theme.semantic;
  return semanticAliasVarsFromInputs({
    isDark: theme.isDark,
    bg: c.bg,
    groupBg: c.group_bg,
    border: c.border,
    text: c.text,
    textSecondary: c.text_secondary,
    label: c.label,
    inputBg: c.input_bg,
    link: s.link,
    success: s.success,
    warning: s.warning,
    error: s.error,
    selectionText: s.selection_text,
    accentOrange: theme.isDark ? darkColorTokens.accentOrange : lightColorTokens.accentOrange,
  });
}

/** Dark standard theme aliases — merged into darkCssVariableMap / :root. */
export const darkSemanticAliasCssVars = semanticAliasVarsFromInputs({
  isDark: true,
  bg: darkColorTokens.bgPrimary,
  groupBg: darkColorTokens.bgSecondary,
  border: darkColorTokens.border,
  text: darkColorTokens.textPrimary,
  textSecondary: darkColorTokens.textSecondary,
  label: darkColorTokens.textMuted,
  inputBg: darkColorTokens.bgSecondary,
  link: darkColorTokens.accentBlue,
  success: darkBadgeTokens.successFg,
  warning: darkBadgeTokens.warningFg,
  error: darkBadgeTokens.dangerFg,
  selectionText: '#ffffff',
  accentOrange: darkColorTokens.accentOrange,
});

/** Light standard theme aliases — merged into lightCssVariableMap / [data-theme=light]. */
export const lightSemanticAliasCssVars = semanticAliasVarsFromInputs({
  isDark: false,
  bg: lightColorTokens.bgPrimary,
  groupBg: lightColorTokens.bgSecondary,
  border: lightColorTokens.border,
  text: lightColorTokens.textPrimary,
  textSecondary: lightColorTokens.textSecondary,
  label: lightColorTokens.textMuted,
  inputBg: lightColorTokens.bgPrimary,
  link: lightColorTokens.accentBlue,
  success: lightBadgeTokens.successFg,
  warning: lightBadgeTokens.warningFg,
  error: lightBadgeTokens.dangerFg,
  selectionText: '#ffffff',
  accentOrange: lightColorTokens.accentOrange,
});
