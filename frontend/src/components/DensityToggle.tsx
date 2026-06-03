/**
 * DensityToggle — Compact / Comfortable switch for dense operator tables.
 *
 * Issue #828. Reads/writes the shared density preference via `useDensity`,
 * which drives the `--density` multiplier on <html>. Rendered in the desktop
 * shell header next to the theme picker.
 */
import React from 'react'
import { useDensity } from '../hooks/useDensity'
import { TouchButton } from '../primitives/TouchButton'

export const DensityToggle: React.FC = () => {
  const { density, toggleDensity } = useDensity()
  const isCompact = density === 'compact'
  return (
    <TouchButton
      type="button"
      id="density-toggle"
      className="density-toggle"
      onClick={toggleDensity}
      pressed={isCompact}
      title={
        isCompact
          ? 'Switch to comfortable density'
          : 'Switch to compact density for dense tables'
      }
    >
      <span aria-hidden="true">
        {/* Density rows glyph — denser bars when compact is active. */}
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="2" y={isCompact ? 2 : 2.5} width="12" height="1.6" rx="0.8" fill="currentColor" />
          <rect x="2" y={isCompact ? 5 : 6.5} width="12" height="1.6" rx="0.8" fill="currentColor" />
          <rect x="2" y={isCompact ? 8 : 10.5} width="12" height="1.6" rx="0.8" fill="currentColor" />
          {isCompact && <rect x="2" y="11" width="12" height="1.6" rx="0.8" fill="currentColor" />}
        </svg>
      </span>
      <span className="density-toggle__label">{isCompact ? 'Compact' : 'Comfortable'}</span>
    </TouchButton>
  )
}
