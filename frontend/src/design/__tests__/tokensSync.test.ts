import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { darkColorTokens, lightColorTokens } from '../tokens'

const css = readFileSync(path.join(__dirname, '../../index.css'), 'utf8')

describe('CSS variables synchronization and global element rules', () => {
  it(':root variables in index.css match darkColorTokens', () => {
    // Extract :root block
    const rootMatch = css.match(/:root\s*\{([^}]+)\}/)
    expect(rootMatch).not.toBeNull()
    const rootContent = rootMatch![1]

    expect(rootContent).toMatch(new RegExp(`--bg-primary:\\s*${darkColorTokens.bgPrimary}`))
    expect(rootContent).toMatch(new RegExp(`--bg-secondary:\\s*${darkColorTokens.bgSecondary}`))
    expect(rootContent).toMatch(new RegExp(`--bg-tertiary:\\s*${darkColorTokens.bgTertiary}`))
    expect(rootContent).toMatch(new RegExp(`--bg-card:\\s*${darkColorTokens.bgCard}`))
    expect(rootContent).toMatch(new RegExp(`--bg-hover:\\s*${darkColorTokens.bgHover}`))
    expect(rootContent).toMatch(new RegExp(`--border:\\s*${darkColorTokens.border}`))
    expect(rootContent).toMatch(new RegExp(`--border-light:\\s*${darkColorTokens.borderLight}`))
    expect(rootContent).toMatch(new RegExp(`--text-primary:\\s*${darkColorTokens.textPrimary}`))
    expect(rootContent).toMatch(new RegExp(`--text-secondary:\\s*${darkColorTokens.textSecondary}`))
    expect(rootContent).toMatch(new RegExp(`--text-muted:\\s*${darkColorTokens.textMuted}`))
  })

  it('[data-theme="light"] in index.css defines light tokens matching lightColorTokens', () => {
    const lightMatch = css.match(/\[data-theme=["']light["']\]\s*\{([^}]+)\}/)
    expect(lightMatch).not.toBeNull()
    const lightContent = lightMatch![1]

    expect(lightContent).toMatch(new RegExp(`--bg-primary:\\s*${lightColorTokens.bgPrimary}`))
    expect(lightContent).toMatch(new RegExp(`--bg-secondary:\\s*${lightColorTokens.bgSecondary}`))
    expect(lightContent).toMatch(new RegExp(`--bg-tertiary:\\s*${lightColorTokens.bgTertiary}`))
    expect(lightContent).toMatch(new RegExp(`--bg-card:\\s*${lightColorTokens.bgCard}`))
    expect(lightContent).toMatch(new RegExp(`--bg-hover:\\s*${lightColorTokens.bgHover}`))
    expect(lightContent).toMatch(new RegExp(`--border:\\s*${lightColorTokens.border}`))
    expect(lightContent).toMatch(new RegExp(`--border-light:\\s*${lightColorTokens.borderLight}`))
    expect(lightContent).toMatch(new RegExp(`--text-primary:\\s*${lightColorTokens.textPrimary}`))
    expect(lightContent).toMatch(new RegExp(`--text-secondary:\\s*${lightColorTokens.textSecondary}`))
    expect(lightContent).toMatch(new RegExp(`--text-muted:\\s*${lightColorTokens.textMuted}`))
  })

  it('declares font scale tokens and text-prose in :root', () => {
    expect(css).toMatch(/--font-size-xs:\s*12px/)
    expect(css).toMatch(/--font-size-sm:\s*13px/)
    expect(css).toMatch(/--font-size-md:\s*14px/)
    expect(css).toMatch(/--font-size-lg:\s*17px/)
    expect(css).toMatch(/--font-size-xl:\s*20px/)
    expect(css).toMatch(/--font-size-2xl:\s*24px/)
    expect(css).toMatch(/--text-prose:/)
  })

  it('applies typography rules to body, headings, and code', () => {
    // Body line-height 1.55
    const bodyMatches = [...css.matchAll(/(?:^|\n)body\s*\{([^}]+)\}/g)]
    const bodyContent = bodyMatches.map((m) => m[1]).join('\n')
    expect(bodyContent).toMatch(/line-height:\s*1\.55/)

    // Headings font-weight 600 and letter-spacing -0.01em
    expect(css).toMatch(/h1,\s*h2,\s*h3,\s*h4/i)
    expect(css).toMatch(/letter-spacing:\s*-0\.01em/)

    // Tabular numbers on table and .tabular
    expect(css).toMatch(/font-variant-numeric:\s*tabular-nums/)
  })

  it('configures global focus-visible with --focus-ring and scrollbars', () => {
    // Focus visible outline
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--focus-ring\)/)
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline-offset:\s*2px/)

    // Scrollbar rules
    expect(css).toMatch(/scrollbar-width:\s*thin/)
    expect(css).toMatch(/::-webkit-scrollbar/)
  })

  it('defines generic buttons with 28px/32px heights and --radius-sm', () => {
    // Default button
    const btnMatch = css.match(/\.button\s*\{([^}]+)\}/)
    expect(btnMatch).not.toBeNull()
    expect(btnMatch![1]).toMatch(/height:\s*32px/)
    expect(btnMatch![1]).toMatch(/border-radius:\s*var\(--radius-sm\)/)

    // Button sm
    const smMatch = css.match(/\.button--sm\s*\{([^}]+)\}/)
    expect(smMatch).not.toBeNull()
    expect(smMatch![1]).toMatch(/height:\s*28px/)

    // Primary, secondary, ghost, danger
    expect(css).toMatch(/\.button--primary/)
    expect(css).toMatch(/\.button--secondary/)
    expect(css).toMatch(/\.button--ghost/)
    expect(css).toMatch(/\.button--danger/)
  })
})
