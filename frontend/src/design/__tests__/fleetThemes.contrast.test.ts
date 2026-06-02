import { describe, it, expect } from 'vitest'
import { FLEET_THEMES, getFleetThemeIds } from '../fleetThemes'

/**
 * Issue #824 — WCAG AA contrast guarantee for every fleet theme.
 *
 * Asserts that the two body-text pairings used everywhere in the UI clear the
 * WCAG 2.1 AA threshold for normal text (4.5:1) against the theme background:
 *   - colors.text          / colors.bg
 *   - colors.text_secondary / colors.bg
 *
 * The accessibility-category themes (high-contrast, daylight-hc) are
 * additionally held to the AAA threshold (7:1) so a regression that softens
 * them is caught here rather than by a screen-reader user.
 *
 * Contrast math is the canonical sRGB relative-luminance formula from the
 * WCAG spec; kept inline (test-only) so production ships no extra code.
 */

const AA_NORMAL = 4.5
const AAA_NORMAL = 7

/** sRGB channel → linearized value per WCAG 2.1. */
function channelLuminance(srgb: number): number {
  const c = srgb / 255
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

/** Relative luminance of a #rrggbb hex color. */
function relativeLuminance(hex: string): number {
  const h = hex.replace('#', '')
  const r = channelLuminance(parseInt(h.slice(0, 2), 16))
  const g = channelLuminance(parseInt(h.slice(2, 4), 16))
  const b = channelLuminance(parseInt(h.slice(4, 6), 16))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio between two hex colors (>= 1). */
export function contrastRatio(fg: string, bg: string): number {
  const l1 = relativeLuminance(fg)
  const l2 = relativeLuminance(bg)
  const lighter = Math.max(l1, l2)
  const darker = Math.min(l1, l2)
  return (lighter + 0.05) / (darker + 0.05)
}

describe('fleet theme WCAG AA contrast', () => {
  const ids = getFleetThemeIds()

  it('exposes every theme via getFleetThemeIds (no orphan definitions)', () => {
    expect(ids.sort()).toEqual(Object.keys(FLEET_THEMES).sort())
    // Issue #824 added six new palettes — guard against accidental removal.
    for (const id of [
      'midnight',
      'nord',
      'solarized-dark',
      'solarized-light',
      'graphite',
      'daylight-hc',
    ]) {
      expect(ids).toContain(id)
    }
  })

  it.each(ids)('theme "%s" clears AA for text/bg and text_secondary/bg', (id) => {
    const { colors } = FLEET_THEMES[id]
    const textRatio = contrastRatio(colors.text, colors.bg)
    const secondaryRatio = contrastRatio(colors.text_secondary, colors.bg)

    expect(
      textRatio,
      `${id}: text ${colors.text} on bg ${colors.bg} = ${textRatio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(AA_NORMAL)
    expect(
      secondaryRatio,
      `${id}: text_secondary ${colors.text_secondary} on bg ${colors.bg} = ${secondaryRatio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(AA_NORMAL)
  })

  it.each(ids.filter((id) => FLEET_THEMES[id].category === 'accessibility'))(
    'accessibility theme "%s" clears the stricter AAA threshold',
    (id) => {
      const { colors } = FLEET_THEMES[id]
      expect(contrastRatio(colors.text, colors.bg)).toBeGreaterThanOrEqual(AAA_NORMAL)
      expect(
        contrastRatio(colors.text_secondary, colors.bg),
      ).toBeGreaterThanOrEqual(AAA_NORMAL)
    },
  )
})
