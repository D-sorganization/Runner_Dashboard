import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useDensity } from '../useDensity'

describe('useDensity — operator-table density preference (#828)', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-density')
  })

  it('defaults to comfortable with no data-density attribute', () => {
    const { result } = renderHook(() => useDensity())
    expect(result.current.density).toBe('comfortable')
    expect(document.documentElement.hasAttribute('data-density')).toBe(false)
  })

  it('sets data-density=compact when toggled to compact', () => {
    const { result } = renderHook(() => useDensity())
    act(() => result.current.setDensity('compact'))
    expect(result.current.density).toBe('compact')
    expect(document.documentElement.getAttribute('data-density')).toBe('compact')
  })

  it('toggles back and forth and clears the attribute on comfortable', () => {
    const { result } = renderHook(() => useDensity())
    act(() => result.current.toggleDensity())
    expect(result.current.density).toBe('compact')
    act(() => result.current.toggleDensity())
    expect(result.current.density).toBe('comfortable')
    expect(document.documentElement.hasAttribute('data-density')).toBe(false)
  })

  it('persists the preference to localStorage', () => {
    const { result } = renderHook(() => useDensity())
    act(() => result.current.setDensity('compact'))
    expect(localStorage.getItem('runner-dashboard:density')).toBe('compact')
  })

  it('rehydrates a stored compact preference on mount', () => {
    localStorage.setItem('runner-dashboard:density', 'compact')
    const { result } = renderHook(() => useDensity())
    expect(result.current.density).toBe('compact')
    expect(document.documentElement.getAttribute('data-density')).toBe('compact')
  })
})
