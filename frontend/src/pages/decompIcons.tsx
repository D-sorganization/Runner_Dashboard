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

/** Activity/pulse glyph (matches legacy `I.activity`). */
export function ActivityGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </Svg>
  );
}

/** Pull-request glyph (matches legacy `I.gitPR`). */
export function GitPrGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={18} cy={18} r={3} />
      <circle cx={6} cy={6} r={3} />
      <path d="M13 6h3a2 2 0 012 2v7M6 9v12" />
    </Svg>
  );
}

/** Issue (circle-with-bang) glyph (matches legacy `I.issue`). */
export function IssueGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={12} cy={12} r={10} />
      <line x1={12} y1={8} x2={12} y2={12} />
      <line x1={12} y1={16} x2={12.01} y2={16} />
    </Svg>
  );
}

/** Play/triangle glyph (matches legacy `I.play`). */
export function PlayGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M5 3l14 9-14 9V3z" />
    </Svg>
  );
}

/** Flask/erlenmeyer glyph (matches legacy `I.flask`). */
export function FlaskGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M9 3h6M10 3v7.4a2 2 0 01-.5 1.3L4 19a2 2 0 001.5 3h13a2 2 0 001.5-3l-5.5-7.3a2 2 0 01-.5-1.3V3" />
    </Svg>
  );
}

/** Gear/settings glyph (matches legacy `I.settings`). */
export function SettingsGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={12} cy={12} r={3} />
      <path d="M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1 1.54V21a2 2 0 11-4 0v-.09a1.7 1.7 0 00-1-1.54 1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.7 1.7 0 00.34-1.87 1.7 1.7 0 00-1.54-1H3a2 2 0 110-4h.09a1.7 1.7 0 001.54-1 1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06a1.7 1.7 0 001.87.34H9a1.7 1.7 0 001-1.54V3a2 2 0 114 0v.09a1.7 1.7 0 001 1.54 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06a1.7 1.7 0 00-.34 1.87V9c.25.61.85 1 1.54 1H21a2 2 0 110 4h-.09a1.7 1.7 0 00-1.54 1z" />
    </Svg>
  );
}

/** Docker-whale-ish stacked-boxes glyph (matches legacy `I.docker`). */
export function DockerGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <rect x={1} y={10} width={22} height={10} rx={2} />
      <rect x={5} y={6} width={4} height={4} />
      <rect x={10} y={6} width={4} height={4} />
      <rect x={10} y={2} width={4} height={4} />
    </Svg>
  );
}
