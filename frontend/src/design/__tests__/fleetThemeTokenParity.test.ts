import { describe, expect, it } from 'vitest'
import { FLEET_THEMES, fleetThemeToCssVars } from '../fleetThemes'
import { darkColorTokens, lightColorTokens } from '../tokens'

// The theme runtime writes these as inline styles on <html>, so they win over
// index.css. The standard Dark/Light themes must emit the token values, or
// token fixes (e.g. the #1719 4.5:1 --text-muted) are silently undone.
const NEUTRALS = {
  '--bg-primary': 'bgPrimary',
  '--bg-secondary': 'bgSecondary',
  '--bg-tertiary': 'bgTertiary',
  '--bg-card': 'bgCard',
  '--bg-hover': 'bgHover',
  '--border': 'border',
  '--border-light': 'borderLight',
  '--text-primary': 'textPrimary',
  '--text-secondary': 'textSecondary',
  '--text-muted': 'textMuted',
} as const

describe('standard fleet themes match design tokens', () => {
  it.each([
    ['dark', darkColorTokens],
    ['light', lightColorTokens],
  ] as const)('%s theme emits the token neutrals', (id, tokens) => {
    const vars = fleetThemeToCssVars(FLEET_THEMES[id])
    for (const [cssVar, key] of Object.entries(NEUTRALS)) {
      expect({ [cssVar]: vars[cssVar] }).toEqual({ [cssVar]: tokens[key] })
    }
  })
})
