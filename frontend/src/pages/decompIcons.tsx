/**
 * decompIcons.tsx — the handful of inline SVG glyphs needed by tabs extracted
 * from the legacy `App.tsx` monolith (decomposition #836).
 *
 * These reproduce, 1:1, the glyphs the legacy `I.refresh` / `I.server`
 * helpers emitted (same paths, stroke, 24×24 viewBox) so the extracted tabs
 * are pixel-identical to the originals. Icons are decorative (`aria-hidden`):
 * the surrounding button/heading carries the accessible name.
 *
 * Kept local to `pages/` rather than lifted to the nav icon set because the
 * nav icons are fixed at 16px / className-only, whereas these need an explicit
 * size to match the legacy call sites.
 */
import React from "react";

interface GlyphProps {
  /** Square size in px (width = height). Defaults to 16. */
  size?: number;
}

function Svg({
  size = 16,
  children,
}: GlyphProps & { children: React.ReactNode }): React.ReactElement {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** Circular-arrows "refresh" glyph (matches legacy `I.refresh`). */
export function RefreshGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M23 4v6h-6M1 20v-6h6" />
      <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
    </Svg>
  );
}

/** Stacked-server glyph (matches legacy `I.server`). */
export function ServerGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <rect x={2} y={2} width={20} height={8} rx={2} />
      <rect x={2} y={14} width={20} height={8} rx={2} />
      <circle cx={6} cy={6} r={1} fill="currentColor" />
      <circle cx={6} cy={18} r={1} fill="currentColor" />
    </Svg>
  );
}

/** Clock-face glyph (matches legacy `I.clock`). */
export function ClockGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={12} cy={12} r={10} />
      <path d="M12 6v6l4 2" />
    </Svg>
  );
}
