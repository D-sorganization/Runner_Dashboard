import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ThemeSettings } from '../ThemeSettings'
import { getFleetThemeIds } from '../../design/fleetThemes'
import { ThemeContext } from '../../design/ThemeContext'

afterEach(cleanup)

describe('ThemeSettings — mounts the full ThemeSelector', () => {
  it('exposes all 13 fleet themes via the embedded selector (not the legacy trio)', () => {
    render(<ThemeSettings />)
    // The selector toggle is present and opens the full category picker.
    fireEvent.click(screen.getByTitle('Change theme'))
    for (const id of getFleetThemeIds()) {
      expect(document.getElementById(`theme-option-${id}`)).not.toBeNull()
    }
  })

  it('still renders the accent-color presets', () => {
    render(<ThemeSettings />)
    expect(screen.getByText('Accent Color')).not.toBeNull()
    expect(screen.getByText('Default')).toHaveAttribute('data-touch-primitive', 'TouchButton')
    expect(screen.getByRole('button', { name: 'Blue accent' })).toHaveClass(
      'theme-settings__accent-swatch--blue',
    )
  })

  it('keeps accent preset values wired to the theme context', () => {
    const setAccentColor = vi.fn()
    render(
      <ThemeContext.Provider
        value={{
          theme: 'dark',
          mode: 'system',
          setMode: vi.fn(),
          accentColor: null,
          setAccentColor,
        }}
      >
        <ThemeSettings />
      </ThemeContext.Provider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Blue accent' }))
    expect(setAccentColor).toHaveBeenCalledWith('#58a6ff')
  })
})
