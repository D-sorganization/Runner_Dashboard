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

/** Stop/square glyph (matches legacy `I.stop`). */
export function StopGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <rect x={6} y={6} width={12} height={12} rx={1} />
    </Svg>
  );
}

/** Up-arrow glyph (matches legacy `I.arrowUp`). */
export function ArrowUpGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M12 19V5M5 12l7-7 7 7" />
    </Svg>
  );
}

/** Down-arrow glyph (matches legacy `I.arrowDown`). */
export function ArrowDownGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M12 5v14M5 12l7 7 7-7" />
    </Svg>
  );
}

/** Warning-triangle glyph, replacing a pictographic warning symbol (epic #1718). */
export function AlertGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M12 3 1 21h22L12 3z" />
      <path d="M12 9v5" />
      <circle cx={12} cy={17} r={0.5} fill="currentColor" stroke="none" />
    </Svg>
  );
}

/** Magnifying-glass glyph, replacing a pictographic search symbol (epic #1718). */
export function SearchGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={11} cy={11} r={7} />
      <path d="M21 21l-4.35-4.35" />
    </Svg>
  );
}

/** Padlock glyph, replacing a pictographic lock symbol (epic #1718). */
export function LockGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <rect x={3} y={11} width={18} height={10} rx={2} />
      <path d="M7 11V7a5 5 0 0110 0v4" />
    </Svg>
  );
}

/** Key glyph, replacing a pictographic key symbol (epic #1718). */
export function KeyGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={7.5} cy={15.5} r={4.5} />
      <path d="M10.6 12.4L21 2M18 5l3 3M15 8l3 3" />
    </Svg>
  );
}

/** Microphone glyph, replacing a pictographic microphone symbol (epic #1718). */
export function MicGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <rect x={9} y={2} width={6} height={12} rx={3} />
      <path d="M5 10v1a7 7 0 0014 0v-1M12 19v3M8 22h8" />
    </Svg>
  );
}

/** Bar-chart glyph, replacing pictographic chart symbols (epic #1718). */
export function ChartGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M3 3v18h18" />
      <path d="M7 15l4-5 3 3 5-7" />
    </Svg>
  );
}

/** Speech-bubble glyph, replacing a pictographic speech-bubble symbol (epic #1718). */
export function ChatGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M21 15a2 2 0 01-2 2H8l-5 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
    </Svg>
  );
}

/** Crescent-moon glyph, replacing a pictographic moon symbol (epic #1718). */
export function MoonGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z" />
    </Svg>
  );
}

/** Sun glyph, replacing a pictographic sun symbol (epic #1718). */
export function SunGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={12} cy={12} r={5} />
      <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
    </Svg>
  );
}

/** Chip/CPU glyph, replacing pictographic hardware symbols (epic #1718). */
export function CpuGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <rect x={6} y={6} width={12} height={12} rx={2} />
      <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
    </Svg>
  );
}

/** Clipboard glyph, replacing a pictographic clipboard symbol (epic #1718). */
export function ClipboardGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <rect x={6} y={4} width={12} height={17} rx={2} />
      <path d="M9 4V3a1 1 0 011-1h4a1 1 0 011 1v1" />
      <path d="M9 11h6M9 15h6" />
    </Svg>
  );
}

/** Octagon "stop" glyph, replacing a pictographic no-entry symbol (epic #1718). */
export function ShieldStopGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <path d="M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86L7.86 2z" />
      <path d="M9 9l6 6M15 9l-6 6" />
    </Svg>
  );
}

/** Gear/settings glyph, replacing a pictographic gear symbol (epic #1718). */
export function GearGlyph({ size }: GlyphProps): React.ReactElement {
  return (
    <Svg size={size}>
      <circle cx={12} cy={12} r={3} />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </Svg>
  );
}

/** Pin glyph, replacing pictographic pin symbols (epic #1718). */
export function PinGlyph({ size, filled }: GlyphProps & { filled?: boolean }): React.ReactElement {
  return (
    <Svg size={size}>
      <path
        d="M12 2a7 7 0 00-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 00-7-7z"
        fill={filled ? 'currentColor' : 'none'}
      />
      <circle cx={12} cy={9} r={2} fill={filled ? 'none' : 'currentColor'} stroke="none" />
    </Svg>
  );
}
