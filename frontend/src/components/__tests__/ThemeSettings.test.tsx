import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ThemeSettings } from '../ThemeSettings'
import { getFleetThemeIds } from '../../design/fleetThemes'

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
    expect(screen.getByText('Default')).not.toBeNull()
  })
})
