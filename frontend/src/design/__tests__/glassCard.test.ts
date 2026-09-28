import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(path.join(__dirname, '../../index.css'), 'utf8')

describe('glass card design foundation (P0 grey flash fix)', () => {
  it('.glass-card has an opaque --bg-card surface with 1px border and --shadow-soft', () => {
    // Look for .glass-card rule block
    const match = css.match(/\.glass-card\s*\{([^}]+)\}/)
    expect(match).not.toBeNull()
    const declarations = match![1]

    expect(declarations).toMatch(/background:\s*var\(--bg-card\)/)
    expect(declarations).toMatch(/border:\s*1px solid var\(--border\)/)
    expect(declarations).toMatch(/box-shadow:\s*var\(--shadow-soft\)/)
  })

  it('no rule applies backdrop-filter to .glass-card', () => {
    // Assert no selector involving .glass-card applies backdrop-filter
    const matches = css.match(/([^{}]+)\{[^}]*backdrop-filter[^}]*\}/g) ?? []
    for (const block of matches) {
      expect(block).not.toMatch(/\.glass-card/)
    }
  })

  it('.glass-card:hover sets no background property', () => {
    // Assert no rule for .glass-card:hover sets background
    const match = css.match(/\.glass-card:hover\s*\{([^}]+)\}/)
    if (match) {
      expect(match[1]).not.toMatch(/background\s*:/)
    }
    expect(css).not.toMatch(/\.glass-card:hover\s*\{[^}]*background\s*:/)
  })

  it('.glass-card--interactive exists and changes only border-color on hover', () => {
    expect(css).toMatch(/\.glass-card--interactive/)
    const hoverMatch = css.match(/\.glass-card--interactive:hover\s*\{([^}]+)\}/)
    expect(hoverMatch).not.toBeNull()
    expect(hoverMatch![1]).toMatch(/border-color:\s*var\(--border-light\)/)
    expect(hoverMatch![1]).not.toMatch(/background/)
  })
})
