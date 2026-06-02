/**
 * Modular type scale (issue #828).
 *
 * Extends the original 5 flat sizes to a 7-step modular ratio
 * (11/12/14/16/20/26/34) and reconciles the keys 1:1 with the `--font-*` CSS
 * variables declared in `index.css` / `typeScaleCssVars`:
 *   micro→--font-micro, meta→--font-meta, body→--font-body,
 *   sectionTitle→--font-section-title, title→--font-title,
 *   headline→--font-headline, display→--font-display.
 */
export const typeScale = {
  micro: "11px",
  meta: "12px",
  body: "14px",
  sectionTitle: "16px",
  title: "20px",
  headline: "26px",
  display: "34px",
} as const;

export const lineHeights = {
  tight: "1.2",
  body: "1.5",
  relaxed: "1.65",
} as const;

export const fontStacks = {
  ui: '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif',
  mono: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
} as const;
