import { readFileSync } from 'node:fs'
import path from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { FONTS_HREF, loadFonts } from '../loadFonts'

describe('non-blocking fonts (#2990)', () => {
  beforeEach(() => {
    document.head.innerHTML = ''
  })

  it('index.html has no render-blocking Google Fonts stylesheet', () => {
    const html = readFileSync(path.join(__dirname, '../../../index.html'), 'utf8')
    expect(html).not.toMatch(/<link[^>]+fonts\.googleapis\.com\/css2/)
    expect(html).toContain('rel="preconnect" href="https://fonts.gstatic.com"')
  })

  it('attaches the stylesheet once with display=swap and no unused weights', () => {
    loadFonts()
    loadFonts()
    const links = document.head.querySelectorAll('link[data-fonts="google"]')
    expect(links).toHaveLength(1)
    expect(FONTS_HREF).toContain('display=swap')
    expect(FONTS_HREF).not.toContain('300')
  })
})
