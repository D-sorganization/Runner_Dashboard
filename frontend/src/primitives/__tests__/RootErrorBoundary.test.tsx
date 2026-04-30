/**
 * Tests for RootErrorBoundary (issue #384).
 *
 * Verifies that:
 * 1. A throw inside a child renders the fallback UI, not a white screen.
 * 2. The bottom nav (simulated here as a sibling) stays alive when one
 *    sub-tree throws (orthogonality).
 * 3. "Try again" button resets boundary state.
 * 4. "Reload dashboard" button triggers window.location.reload().
 */

import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { RootErrorBoundary } from '../RootErrorBoundary'

// Suppress React error boundary console.error noise in test output
const originalConsoleError = console.error
beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => {
  console.error = originalConsoleError
  vi.restoreAllMocks()
})

// A component that throws on demand
function BombComponent({ shouldThrow }: { shouldThrow: boolean }): JSX.Element {
  if (shouldThrow) {
    throw new Error('Test explosion')
  }
  return <div data-testid="ok">All good</div>
}

describe('RootErrorBoundary', () => {
  it('renders children when no error', () => {
    render(
      <RootErrorBoundary>
        <BombComponent shouldThrow={false} />
      </RootErrorBoundary>
    )
    expect(screen.getByTestId('ok')).toBeDefined()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders fallback UI when child throws', () => {
    render(
      <RootErrorBoundary>
        <BombComponent shouldThrow={true} />
      </RootErrorBoundary>
    )
    const alert = screen.getByRole('alert')
    expect(alert).toBeDefined()
    expect(alert.textContent).toContain('Something went wrong')
  })

  it('shows error message in details section', () => {
    render(
      <RootErrorBoundary>
        <BombComponent shouldThrow={true} />
      </RootErrorBoundary>
    )
    // Open details
    const summary = screen.getByText('Show details')
    fireEvent.click(summary)
    expect(document.body.textContent).toContain('Test explosion')
  })

  it('"Try again" button resets the error state', () => {
    // Use a ref to toggle throw after reset
    let throwFlag = true
    function ToggleBomb(): JSX.Element {
      if (throwFlag) throw new Error('boom')
      return <div data-testid="recovered">Recovered</div>
    }

    const { rerender } = render(
      <RootErrorBoundary>
        <ToggleBomb />
      </RootErrorBoundary>
    )
    expect(screen.getByRole('alert')).toBeDefined()

    throwFlag = false
    fireEvent.click(screen.getByText('Try again'))
    rerender(
      <RootErrorBoundary>
        <ToggleBomb />
      </RootErrorBoundary>
    )
    expect(screen.getByTestId('recovered')).toBeDefined()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('calls window.location.reload on "Reload dashboard"', () => {
    const reloadMock = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { ...window.location, reload: reloadMock },
      writable: true,
    })

    render(
      <RootErrorBoundary>
        <BombComponent shouldThrow={true} />
      </RootErrorBoundary>
    )
    fireEvent.click(screen.getByText('Reload dashboard'))
    expect(reloadMock).toHaveBeenCalledOnce()
  })

  it('surfaces requestId when provided', () => {
    render(
      <RootErrorBoundary requestId="req-abc-123">
        <BombComponent shouldThrow={true} />
      </RootErrorBoundary>
    )
    expect(screen.getByText('req-abc-123')).toBeDefined()
  })

  it('orthogonality: sibling component stays mounted when boundary child throws', () => {
    render(
      <div>
        <div data-testid="nav">Bottom Nav</div>
        <RootErrorBoundary>
          <BombComponent shouldThrow={true} />
        </RootErrorBoundary>
      </div>
    )
    // Nav is outside boundary — must survive
    expect(screen.getByTestId('nav')).toBeDefined()
    // Boundary shows fallback
    expect(screen.getByRole('alert')).toBeDefined()
  })
})
