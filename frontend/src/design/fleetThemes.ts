/**
 * Fleet Shared Theme Definitions
 *
 * This module loads the canonical themes.json from the Tools repository
 * and maps the fleet theme tokens onto the Dashboard's CSS variable
 * namespace, enabling all 13 built-in themes + custom themes.
 *
 * Architecture:
 *   themes.json (Tools repo) → fleetThemes.ts → ThemeProvider → CSS variables
 *
 * Addresses: Runner_Dashboard#618 (Color Theme Management)
 */

/* ── Fleet Theme Types ──────────────────────────────────────────────── */

export interface FleetThemeColors {
  bg: string;
  group_bg: string;
  border: string;
  text: string;
  text_secondary: string;
  label: string;
  focus: string;
  input_bg: string;
  accent: string;
  title_bg: string;
  title_border: string;
  table_header: string;
  table_alt: string;
  button_hover: string;
}

export interface FleetSemanticColors {
  success: string;
  warning: string;
  error: string;
  info: string;
  link: string;
  link_hover: string;
  selection_bg: string;
  selection_text: string;
}

export interface FleetThemeDef {
  name: string;
  category: string;
  isDark: boolean;
  colors: FleetThemeColors;
  semantic: FleetSemanticColors;
}

export type FleetThemeId =
  | 'light'
  | 'dark'
  | 'slate-gray'
  | 'ocean-blue'
  | 'forest-green'
  | 'monokai'
  | 'dracula'
  | 'one-dark'
  | 'gitpod-dark'
  | 'ms-word'
  | 'ms-excel'
  | 'legal-pad'
  | 'high-contrast';

/* ── Built-in Fleet Themes ──────────────────────────────────────────── */
// Embedded from Tools/src/shared/theme-definitions/themes.json (v2.0)
// This avoids a runtime dependency on the Tools repo; the canonical
// source is themes.json and any additions there should be mirrored here.

export const FLEET_THEMES: Record<FleetThemeId, FleetThemeDef> = {
  light: {
    name: 'Light', category: 'standard', isDark: false,
    colors: { bg: '#ffffff', group_bg: '#f8f9fa', border: '#ced4da', text: '#212529', text_secondary: '#495057', label: '#6c757d', focus: '#80bdff', input_bg: '#ffffff', accent: '#5a8fc4', title_bg: '#e3f2fd', title_border: '#90caf9', table_header: '#e9ecef', table_alt: '#f8f9fa', button_hover: '#4a7ba7' },
    semantic: { success: '#28a745', warning: '#ffc107', error: '#dc3545', info: '#17a2b8', link: '#0066cc', link_hover: '#004499', selection_bg: '#0078d4', selection_text: '#ffffff' },
  },
  dark: {
    name: 'Dark', category: 'standard', isDark: true,
    colors: { bg: '#1a1d23', group_bg: '#24272e', border: '#3a3f4a', text: '#e1e4e8', text_secondary: '#c9d1d9', label: '#8b949e', focus: '#58a6ff', input_bg: '#0d1117', accent: '#4a7ba7', title_bg: '#2d3748', title_border: '#4a7ba7', table_header: '#2d3748', table_alt: '#24272e', button_hover: '#5a8fc4' },
    semantic: { success: '#3fb950', warning: '#d29922', error: '#f85149', info: '#58a6ff', link: '#58a6ff', link_hover: '#79b8ff', selection_bg: '#264f78', selection_text: '#ffffff' },
  },
  'slate-gray': {
    name: 'Slate Gray', category: 'neutral', isDark: false,
    colors: { bg: '#f5f5f5', group_bg: '#ebebeb', border: '#c0c0c0', text: '#333333', text_secondary: '#4a4a4a', label: '#666666', focus: '#555555', input_bg: '#ffffff', accent: '#546e7a', title_bg: '#cfd8dc', title_border: '#78909c', table_header: '#e0e0e0', table_alt: '#f5f5f5', button_hover: '#455a64' },
    semantic: { success: '#4caf50', warning: '#ff9800', error: '#f44336', info: '#2196f3', link: '#37474f', link_hover: '#263238', selection_bg: '#546e7a', selection_text: '#ffffff' },
  },
  'ocean-blue': {
    name: 'Ocean Blue', category: 'nature', isDark: false,
    colors: { bg: '#e8f4f8', group_bg: '#d0e8f2', border: '#90c4d4', text: '#0d3b4f', text_secondary: '#1a5570', label: '#2d6a85', focus: '#3498db', input_bg: '#ffffff', accent: '#2980b9', title_bg: '#b8dce8', title_border: '#5dade2', table_header: '#c8e6f5', table_alt: '#d8ecf5', button_hover: '#1f6a8a' },
    semantic: { success: '#27ae60', warning: '#f39c12', error: '#e74c3c', info: '#3498db', link: '#2980b9', link_hover: '#1f6a8a', selection_bg: '#2980b9', selection_text: '#ffffff' },
  },
  'forest-green': {
    name: 'Forest Green', category: 'nature', isDark: false,
    colors: { bg: '#f0f5f0', group_bg: '#e0ebe0', border: '#a8c4a8', text: '#1e4620', text_secondary: '#2d5a2f', label: '#3d6b3f', focus: '#4caf50', input_bg: '#ffffff', accent: '#388e3c', title_bg: '#c8e6c9', title_border: '#66bb6a', table_header: '#d5ead6', table_alt: '#e5f0e5', button_hover: '#2e7d32' },
    semantic: { success: '#2e7d32', warning: '#f57c00', error: '#d32f2f', info: '#1976d2', link: '#388e3c', link_hover: '#2e7d32', selection_bg: '#388e3c', selection_text: '#ffffff' },
  },
  monokai: {
    name: 'Monokai', category: 'editor', isDark: true,
    colors: { bg: '#272822', group_bg: '#3e3d32', border: '#75715e', text: '#f8f8f2', text_secondary: '#ae81ff', label: '#e6db74', focus: '#a6e22e', input_bg: '#171814', accent: '#f92672', title_bg: '#383830', title_border: '#f92672', table_header: '#3e3d32', table_alt: '#272822', button_hover: '#e6db74' },
    semantic: { success: '#a6e22e', warning: '#e6db74', error: '#f92672', info: '#66d9ef', link: '#66d9ef', link_hover: '#ae81ff', selection_bg: '#49483e', selection_text: '#f8f8f2' },
  },
  dracula: {
    name: 'Dracula', category: 'editor', isDark: true,
    colors: { bg: '#282a36', group_bg: '#343746', border: '#6272a4', text: '#f8f8f2', text_secondary: '#bd93f9', label: '#8be9fd', focus: '#ff79c6', input_bg: '#191a21', accent: '#ff5555', title_bg: '#44475a', title_border: '#bd93f9', table_header: '#44475a', table_alt: '#282a36', button_hover: '#ff79c6' },
    semantic: { success: '#50fa7b', warning: '#f1fa8c', error: '#ff5555', info: '#8be9fd', link: '#8be9fd', link_hover: '#ff79c6', selection_bg: '#44475a', selection_text: '#f8f8f2' },
  },
  'one-dark': {
    name: 'One Dark', category: 'editor', isDark: true,
    colors: { bg: '#282c34', group_bg: '#30363f', border: '#5c6370', text: '#abb2bf', text_secondary: '#56b6c2', label: '#e5c07b', focus: '#61afef', input_bg: '#21252b', accent: '#98c379', title_bg: '#353b45', title_border: '#e06c75', table_header: '#353b45', table_alt: '#282c34', button_hover: '#c678dd' },
    semantic: { success: '#98c379', warning: '#e5c07b', error: '#e06c75', info: '#56b6c2', link: '#61afef', link_hover: '#528bff', selection_bg: '#3e4451', selection_text: '#abb2bf' },
  },
  'gitpod-dark': {
    name: 'Gitpod Dark', category: 'editor', isDark: true,
    colors: { bg: '#0d1117', group_bg: '#161b22', border: '#30363d', text: '#c9d1d9', text_secondary: '#8b949e', label: '#ffb45b', focus: '#12b5cb', input_bg: '#010409', accent: '#12b5cb', title_bg: '#21262d', title_border: '#ffb45b', table_header: '#21262d', table_alt: '#161b22', button_hover: '#0e9dab' },
    semantic: { success: '#3fb950', warning: '#ff9a3c', error: '#f85149', info: '#58a6ff', link: '#ff9a3c', link_hover: '#ffb45b', selection_bg: '#ff9a3c30', selection_text: '#e6edf3' },
  },
  'ms-word': {
    name: 'MS Word', category: 'office', isDark: false,
    colors: { bg: '#ffffff', group_bg: '#f3f3f3', border: '#d1d1d1', text: '#000000', text_secondary: '#333333', label: '#666666', focus: '#2b579a', input_bg: '#ffffff', accent: '#2b579a', title_bg: '#deecf9', title_border: '#2b579a', table_header: '#e6e6e6', table_alt: '#f9f9f9', button_hover: '#1e3f6f' },
    semantic: { success: '#107c10', warning: '#ffb900', error: '#d13438', info: '#0078d4', link: '#2b579a', link_hover: '#1e3f6f', selection_bg: '#2b579a', selection_text: '#ffffff' },
  },
  'ms-excel': {
    name: 'MS Excel', category: 'office', isDark: false,
    colors: { bg: '#ffffff', group_bg: '#f3f3f3', border: '#d1d1d1', text: '#000000', text_secondary: '#333333', label: '#666666', focus: '#217346', input_bg: '#ffffff', accent: '#217346', title_bg: '#e2f0d9', title_border: '#217346', table_header: '#e6e6e6', table_alt: '#f0f7ec', button_hover: '#185c37' },
    semantic: { success: '#217346', warning: '#ffb900', error: '#d13438', info: '#0078d4', link: '#217346', link_hover: '#185c37', selection_bg: '#217346', selection_text: '#ffffff' },
  },
  'legal-pad': {
    name: 'Legal Pad', category: 'office', isDark: false,
    colors: { bg: '#ffffc0', group_bg: '#fff8a8', border: '#d4c97a', text: '#2d2d00', text_secondary: '#4a4a00', label: '#6b6b00', focus: '#b8860b', input_bg: '#fffff0', accent: '#b8860b', title_bg: '#fff59d', title_border: '#c9a227', table_header: '#f5e6a3', table_alt: '#fffacd', button_hover: '#8b6914' },
    semantic: { success: '#558b2f', warning: '#f9a825', error: '#c62828', info: '#0277bd', link: '#8b6914', link_hover: '#6b5000', selection_bg: '#b8860b', selection_text: '#ffffff' },
  },
  'high-contrast': {
    name: 'High Contrast', category: 'accessibility', isDark: true,
    colors: { bg: '#000000', group_bg: '#1a1a1a', border: '#ffffff', text: '#ffffff', text_secondary: '#ffffff', label: '#00ffff', focus: '#00ffff', input_bg: '#000000', accent: '#00ffff', title_bg: '#333333', title_border: '#00ffff', table_header: '#333333', table_alt: '#1a1a1a', button_hover: '#00cccc' },
    semantic: { success: '#00ff00', warning: '#ffff00', error: '#ff0000', info: '#00ffff', link: '#00ffff', link_hover: '#00cccc', selection_bg: '#00ffff', selection_text: '#000000' },
  },
};

/* ── Mapping: Fleet Tokens → Dashboard CSS Variables ────────────────── */

/**
 * Convert a fleet theme into the Dashboard's CSS variable namespace.
 * This bridges the fleet's `themes.json` color keys with the
 * `--bg-primary`, `--accent-blue`, etc. variables already used in index.css.
 */
export function fleetThemeToCssVars(theme: FleetThemeDef): Record<string, string> {
  const c = theme.colors;
  const s = theme.semantic;

  // Derive glassmorphism values based on dark/light
  const glassBg = theme.isDark
    ? `rgba(${hexToRgb(c.group_bg)}, 0.7)`
    : `rgba(${hexToRgb(c.bg)}, 0.7)`;
  const glassBorder = theme.isDark
    ? 'rgba(255, 255, 255, 0.1)'
    : 'rgba(0, 0, 0, 0.08)';
  const glassBorderLight = theme.isDark
    ? 'rgba(255, 255, 255, 0.05)'
    : 'rgba(0, 0, 0, 0.05)';
  const glassShadow = theme.isDark
    ? '0 8px 32px 0 rgba(0, 0, 0, 0.37)'
    : '0 8px 32px 0 rgba(0, 0, 0, 0.1)';

  return {
    '--bg-primary': c.bg,
    '--bg-secondary': c.group_bg,
    '--bg-tertiary': c.table_alt,
    '--bg-card': c.group_bg,
    '--bg-hover': c.button_hover + '22', // 13% opacity hover
    '--border': c.border,
    '--border-light': c.title_border,
    '--text-primary': c.text,
    '--text-secondary': c.text_secondary,
    '--text-muted': c.label,
    '--accent-blue': c.focus,
    '--accent-green': s.success,
    '--accent-red': s.error,
    '--accent-yellow': s.warning,
    '--accent-purple': c.accent,
    '--accent-orange': s.warning,
    '--glass-bg': glassBg,
    '--glass-border': glassBorder,
    '--glass-border-light': glassBorderLight,
    '--glass-shadow': glassShadow,
    // Badge tokens derived from semantic colors
    '--badge-success-bg': `${s.success}26`,
    '--badge-success-fg': s.success,
    '--badge-warning-bg': `${s.warning}26`,
    '--badge-warning-fg': s.warning,
    '--badge-danger-bg': `${s.error}26`,
    '--badge-danger-fg': s.error,
    '--badge-info-bg': `${s.info}26`,
    '--badge-info-fg': s.info,
    '--badge-neutral-bg': `${c.label}26`,
    '--badge-neutral-fg': c.label,
  };
}

/** Convert a hex color to comma-separated RGB values. */
function hexToRgb(hex: string): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `${r}, ${g}, ${b}`;
}

/** Get all available fleet theme IDs. */
export function getFleetThemeIds(): FleetThemeId[] {
  return Object.keys(FLEET_THEMES) as FleetThemeId[];
}

/** Get theme display name from ID. */
export function getFleetThemeDisplayName(themeId: FleetThemeId): string {
  return FLEET_THEMES[themeId]?.name ?? themeId;
}

/** Check if a fleet theme is dark. */
export function isFleetThemeDark(themeId: FleetThemeId): boolean {
  return FLEET_THEMES[themeId]?.isDark ?? false;
}

/** Get themes grouped by category. */
export function getFleetThemesByCategory(): Record<string, FleetThemeId[]> {
  const categories: Record<string, FleetThemeId[]> = {};
  for (const [id, theme] of Object.entries(FLEET_THEMES)) {
    const cat = theme.category;
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(id as FleetThemeId);
  }
  return categories;
}
