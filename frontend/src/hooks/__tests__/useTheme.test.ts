import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useTheme } from '../useTheme'
import { FLEET_THEMES, fleetThemeToCssVars } from '../../design/fleetThemes'

/**
 * Install a matchMedia stub that reports a fixed system preference so
 * `mode === 'system'` resolves deterministically in jsdom.
 */
function stubMatchMedia(prefersDark: boolean): void {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    media: query,
    matches: prefersDark,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia
}

describe('useTheme — single source of truth for CSS variables', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('style')
    document.documentElement.removeAttribute('data-theme')
    stubMatchMedia(true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('writes the resolved fleet theme vars onto document.documentElement', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setMode('dracula'))

    const expected = fleetThemeToCssVars(FLEET_THEMES.dracula)
    const root = document.documentElement
    expect(root.style.getPropertyValue('--bg-primary')).toBe(expected['--bg-primary'])
    expect(root.getAttribute('data-theme')).toBe('dark')
  })

  it('sets data-theme=light for a light fleet theme', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setMode('ms-word'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('layers a custom accent over the resolved theme and survives theme switches', () => {
    const { result } = renderHook(() => useTheme())
    const root = document.documentElement

    act(() => result.current.setAccentColor('#ff00ff'))
    expect(root.style.getPropertyValue('--accent-blue')).toBe('#ff00ff')
    expect(root.style.getPropertyValue('--badge-info-fg')).toBe('#ff00ff')

    // Switching themes must NOT clobber the accent override.
    act(() => result.current.setMode('monokai'))
    expect(root.style.getPropertyValue('--accent-blue')).toBe('#ff00ff')
  })

  it('clears the accent override back to the theme accent when set to null', () => {
    const { result } = renderHook(() => useTheme())
    const root = document.documentElement

    act(() => result.current.setMode('dracula'))
    act(() => result.current.setAccentColor('#ff00ff'))
    expect(root.style.getPropertyValue('--accent-blue')).toBe('#ff00ff')

    act(() => result.current.setAccentColor(null))
    const themeAccent = fleetThemeToCssVars(FLEET_THEMES.dracula)['--accent-blue']
    expect(root.style.getPropertyValue('--accent-blue')).toBe(themeAccent)
  })

  it('persists the selected mode to localStorage', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setMode('one-dark'))
    expect(localStorage.getItem('runner-dashboard:theme-mode')).toBe('one-dark')
  })
})
