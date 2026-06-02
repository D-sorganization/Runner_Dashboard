import { describe, it, expect } from 'vitest'
import {
  radii,
  shadows,
  statusTokens,
  radiiCssVars,
  shadowsCssVars,
  statusCssVars,
  typeScaleCssVars,
  elevationCssVars,
  elevationToCssVariables,
} from '../tokens'
import { typeScale } from '../type'

/**
 * Issue #827 / #828 — the elevation system and modular type scale are now
 * first-class CSS variables (previously `radii`/`shadows`/`statusTokens` were
 * exported but unconsumed). These tests pin the var contract so a drift between
 * the typed tokens and the `--*` names breaks here rather than silently.
 */
describe('elevation + type CSS variable contract', () => {
  it('promotes every radius token to a --radius-* var', () => {
    expect(radiiCssVars).toEqual({
      '--radius-sm': radii.sm,
      '--radius-md': radii.md,
      '--radius-lg': radii.lg,
      '--radius-pill': radii.pill,
    })
  })

  it('promotes every shadow token to a --shadow-* var', () => {
    expect(shadowsCssVars).toEqual({
      '--shadow-soft': shadows.soft,
      '--shadow-card': shadows.card,
      '--shadow-modal': shadows.modal,
    })
  })

  it('promotes every status token to paired --status-*-bg/fg vars', () => {
    for (const [name, pair] of Object.entries(statusTokens)) {
      expect(statusCssVars[`--status-${name}-bg` as keyof typeof statusCssVars]).toBe(pair.bg)
      expect(statusCssVars[`--status-${name}-fg` as keyof typeof statusCssVars]).toBe(pair.fg)
    }
  })

  it('type scale is a 7-step modular ratio reconciled with --font-* vars', () => {
    expect(Object.values(typeScale)).toEqual([
      '11px',
      '12px',
      '14px',
      '16px',
      '20px',
      '26px',
      '34px',
    ])
    expect(typeScaleCssVars['--font-headline']).toBe('26px')
    expect(typeScaleCssVars['--font-display']).toBe('34px')
  })

  it('elevationToCssVariables serialises every elevation var', () => {
    const css = elevationToCssVariables()
    for (const name of Object.keys(elevationCssVars)) {
      expect(css).toContain(`${name}:`)
    }
  })
})
