/**
 * useDensity — operator-table density preference (issue #828).
 *
 * Toggles a `--density` multiplier (1 = Comfortable, 0.82 = Compact) by setting
 * `data-density` on <html>. The actual padding math lives in index.css; this
 * hook is the typed, persisted source of truth for the preference.
 *
 * Mirrors the persistence shape of useTheme (localStorage, SSR-safe guards) so
 * the two display preferences behave consistently.
 */
import { useCallback, useEffect, useState } from 'react'

export type Density = 'comfortable' | 'compact'

const STORAGE_KEY = 'runner-dashboard:density'

function getStoredDensity(): Density {
  if (typeof window === 'undefined') return 'comfortable'
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === 'compact' || raw === 'comfortable') return raw
  } catch {
    // ignore
  }
  return 'comfortable'
}

/** Apply the density preference to <html data-density>. Compact only. */
export function applyDensity(density: Density): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (density === 'compact') {
    root.setAttribute('data-density', 'compact')
  } else {
    root.removeAttribute('data-density')
  }
}

export function useDensity() {
  const [density, setDensityState] = useState<Density>(() => getStoredDensity())

  useEffect(() => {
    applyDensity(density)
  }, [density])

  const setDensity = useCallback((next: Density) => {
    setDensityState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // storage unavailable — non-fatal
    }
  }, [])

  const toggleDensity = useCallback(() => {
    setDensity(density === 'compact' ? 'comfortable' : 'compact')
  }, [density, setDensity])

  return { density, setDensity, toggleDensity }
}
