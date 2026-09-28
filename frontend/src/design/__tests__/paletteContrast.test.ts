import { describe, expect, it } from 'vitest'
import {
  darkColorTokens,
  lightColorTokens,
  darkBadgeTokens,
  lightBadgeTokens,
} from '../tokens'

/**
 * WCAG 2.1 relative luminance and contrast ratio calculation.
 */
function channelLuminance(srgb: number): number {
  const c = srgb / 255
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

function relativeLuminance(hex: string): number {
  const h = hex.replace('#', '')
  const r = channelLuminance(parseInt(h.slice(0, 2), 16))
  const g = channelLuminance(parseInt(h.slice(2, 4), 16))
  const b = channelLuminance(parseInt(h.slice(4, 6), 16))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrastRatio(hex1: string, hex2: string): number {
  const l1 = relativeLuminance(hex1)
  const l2 = relativeLuminance(hex2)
  const lighter = Math.max(l1, l2)
  const darker = Math.min(l1, l2)
  return (lighter + 0.05) / (darker + 0.05)
}

/** Simulate blending a foreground hex with alpha onto a background hex. */
function blendHex(fgColor: string, alpha: number, bgColor: string): string {
  const fgH = fgColor.replace('#', '')
  const bgH = bgColor.replace('#', '')
  const r = Math.round(
    parseInt(bgH.slice(0, 2), 16) * (1 - alpha) + parseInt(fgH.slice(0, 2), 16) * alpha,
  )
  const g = Math.round(
    parseInt(bgH.slice(2, 4), 16) * (1 - alpha) + parseInt(fgH.slice(2, 4), 16) * alpha,
  )
  const b = Math.round(
    parseInt(bgH.slice(4, 6), 16) * (1 - alpha) + parseInt(fgH.slice(4, 6), 16) * alpha,
  )
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`
}

describe('Design Foundation: Palette WCAG AA Contrast', () => {
  describe('Dark palette contrast', () => {
    it('primary text on bgPrimary and bgCard clears 4.5:1 (WCAG AA normal text)', () => {
      const ratioPrimary = contrastRatio(darkColorTokens.textPrimary, darkColorTokens.bgPrimary)
      const ratioCard = contrastRatio(darkColorTokens.textPrimary, darkColorTokens.bgCard)
      expect(ratioPrimary).toBeGreaterThanOrEqual(4.5)
      expect(ratioCard).toBeGreaterThanOrEqual(4.5)
    })

    it('secondary text on bgPrimary and bgCard clears 4.5:1', () => {
      const ratioPrimary = contrastRatio(darkColorTokens.textSecondary, darkColorTokens.bgPrimary)
      const ratioCard = contrastRatio(darkColorTokens.textSecondary, darkColorTokens.bgCard)
      expect(ratioPrimary).toBeGreaterThanOrEqual(4.5)
      expect(ratioCard).toBeGreaterThanOrEqual(4.5)
    })

    it('muted text clears 4.5:1 on every dark surface (#833, axe color-contrast)', () => {
      for (const bg of [darkColorTokens.bgPrimary, darkColorTokens.bgSecondary, darkColorTokens.bgTertiary, darkColorTokens.bgCard]) {
        expect(contrastRatio(darkColorTokens.textMuted, bg)).toBeGreaterThanOrEqual(4.5)
      }
    })

    it('uses warm neutral charcoal base rather than navy', () => {
      // Warm neutral charcoal has approximately equal R, G, B channels with R/G >= B
      const hex = darkColorTokens.bgPrimary.replace('#', '')
      const r = parseInt(hex.slice(0, 2), 16)
      const g = parseInt(hex.slice(2, 4), 16)
      const b = parseInt(hex.slice(4, 6), 16)
      // Navy would have b significantly higher than r and g
      expect(Math.abs(r - g)).toBeLessThanOrEqual(2)
      expect(b - r).toBeLessThanOrEqual(3)
      expect(darkColorTokens.bgPrimary).toBe('#111113')
      expect(darkColorTokens.bgCard).toBe('#18181b')
    })
  })

  describe('Light palette contrast', () => {
    it('primary text on bgPrimary and bgCard clears 4.5:1', () => {
      const ratioPrimary = contrastRatio(lightColorTokens.textPrimary, lightColorTokens.bgPrimary)
      const ratioCard = contrastRatio(lightColorTokens.textPrimary, lightColorTokens.bgCard)
      expect(ratioPrimary).toBeGreaterThanOrEqual(4.5)
      expect(ratioCard).toBeGreaterThanOrEqual(4.5)
    })

    it('secondary text on bgPrimary and bgCard clears 4.5:1', () => {
      const ratioPrimary = contrastRatio(lightColorTokens.textSecondary, lightColorTokens.bgPrimary)
      const ratioCard = contrastRatio(lightColorTokens.textSecondary, lightColorTokens.bgCard)
      expect(ratioPrimary).toBeGreaterThanOrEqual(4.5)
      expect(ratioCard).toBeGreaterThanOrEqual(4.5)
    })

    it('muted text clears 4.5:1 on every light surface', () => {
      for (const bg of [lightColorTokens.bgPrimary, lightColorTokens.bgSecondary, lightColorTokens.bgTertiary, lightColorTokens.bgCard]) {
        expect(contrastRatio(lightColorTokens.textMuted, bg)).toBeGreaterThanOrEqual(4.5)
      }
    })

    it('uses clean near-white base', () => {
      expect(lightColorTokens.bgPrimary).toBe('#ffffff')
      expect(lightColorTokens.bgSecondary).toBe('#fafafa')
      expect(lightColorTokens.bgTertiary).toBe('#f4f4f5')
      expect(lightColorTokens.border).toBe('#e4e4e7')
    })
  })

  describe('Status and badge colors contrast', () => {
    it('dark badge foregrounds clear AA (4.5:1) on 15% tint over bgCard', () => {
      const bgCard = darkColorTokens.bgCard
      const pairs = [
        { name: 'success', fg: darkBadgeTokens.successFg },
        { name: 'warning', fg: darkBadgeTokens.warningFg },
        { name: 'danger', fg: darkBadgeTokens.dangerFg },
        { name: 'info', fg: darkBadgeTokens.infoFg },
      ]

      for (const { name, fg } of pairs) {
        const blended = blendHex(fg, 0.15, bgCard)
        const ratio = contrastRatio(fg, blended)
        expect(ratio, `dark ${name} fg ${fg} on tint ${blended}`).toBeGreaterThanOrEqual(4.5)
      }
    })

    it('light badge foregrounds clear AA (4.5:1) on 12% tint over bgPrimary', () => {
      const bgPrimary = lightColorTokens.bgPrimary
      const pairs = [
        { name: 'success', fg: lightBadgeTokens.successFg },
        { name: 'warning', fg: lightBadgeTokens.warningFg },
        { name: 'danger', fg: lightBadgeTokens.dangerFg },
        { name: 'info', fg: lightBadgeTokens.infoFg },
      ]

      for (const { name, fg } of pairs) {
        const blended = blendHex(fg, 0.12, bgPrimary)
        const ratio = contrastRatio(fg, blended)
        expect(ratio, `light ${name} fg ${fg} on tint ${blended}`).toBeGreaterThanOrEqual(4.5)
      }
    })
  })
})
