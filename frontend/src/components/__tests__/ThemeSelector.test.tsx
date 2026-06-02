import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ThemeSelector } from '../ThemeSelector'
import { getFleetThemeIds } from '../../design/fleetThemes'

afterEach(cleanup)

describe('ThemeSelector — exposes all 13 fleet themes', () => {
  it('renders an option for System plus every fleet theme', () => {
    render(<ThemeSelector currentMode="system" onThemeChange={() => {}} />)
    fireEvent.click(screen.getByTitle('Change theme'))

    expect(document.getElementById('theme-option-system')).not.toBeNull()
    for (const id of getFleetThemeIds()) {
      expect(document.getElementById(`theme-option-${id}`)).not.toBeNull()
    }
  })

  it('invokes onThemeChange with the chosen fleet theme id', () => {
    const onChange = vi.fn()
    render(<ThemeSelector currentMode="system" onThemeChange={onChange} />)
    fireEvent.click(screen.getByTitle('Change theme'))
    fireEvent.click(document.getElementById('theme-option-dracula')!)
    expect(onChange).toHaveBeenCalledWith('dracula')
  })

  it('can select a theme outside the legacy system/light/dark trio', () => {
    const onChange = vi.fn()
    render(<ThemeSelector currentMode="system" onThemeChange={onChange} />)
    fireEvent.click(screen.getByTitle('Change theme'))
    fireEvent.click(document.getElementById('theme-option-high-contrast')!)
    expect(onChange).toHaveBeenCalledWith('high-contrast')
  })
})
