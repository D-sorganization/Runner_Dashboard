import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MobileShell } from '../MobileShell'

describe('MobileShell', () => {
  beforeEach(() => {
    // Mock window.matchMedia for viewport detection — mobile breakpoint
    window.matchMedia = vi.fn((query) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders bottom tabs on mobile viewport', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    expect(screen.getByText('Fleet')).toBeInTheDocument()
    expect(screen.getByText('Workflows')).toBeInTheDocument()
    expect(screen.getByText('Remediation')).toBeInTheDocument()
    expect(screen.getByText('Maxwell')).toBeInTheDocument()
    expect(screen.getByText('More')).toBeInTheDocument()
  })

  it('highlights active tab', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="workflows" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const workflowsTab = screen.getByText('Workflows').closest('button')
    expect(workflowsTab).toHaveStyle({ color: '#58a6ff' }) // accentBlue
  })

  it('calls onTabChange when tab is clicked', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const workflowsTab = screen.getByText('Workflows')
    fireEvent.click(workflowsTab)

    expect(handleTabChange).toHaveBeenCalledWith('workflows')
  })

  it('opens drawer when More tab is clicked', async () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const moreTab = screen.getByText('More')
    fireEvent.click(moreTab)

    await waitFor(() => {
      expect(screen.getByText('Org')).toBeInTheDocument()
      expect(screen.getByText('Queue Health')).toBeInTheDocument()
    })
  })

  it('closes drawer when backdrop is clicked', async () => {
    const handleTabChange = vi.fn()
    const { container } = render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const moreTab = screen.getByText('More')
    fireEvent.click(moreTab)

    await waitFor(() => {
      expect(screen.getByText('Org')).toBeInTheDocument()
    })

    const overlay = container.querySelector('[style*="rgba(0, 0, 0, 0.5)"]')
    if (overlay) {
      fireEvent.click(overlay)
    }

    await waitFor(() => {
      expect(screen.queryByText('Org')).not.toBeInTheDocument()
    })
  })

  it('preserves component state when switching tabs', () => {
    const handleTabChange = vi.fn()
    const { rerender } = render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <Counter />
      </MobileShell>
    )

    const incrementBtn = screen.getByText('+')
    fireEvent.click(incrementBtn)
    fireEvent.click(incrementBtn)

    expect(screen.getByText('Count: 2')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Workflows'))
    rerender(
      <MobileShell currentTab="workflows" onTabChange={handleTabChange}>
        <Counter />
      </MobileShell>
    )

    expect(screen.getByText('Count: 2')).toBeInTheDocument()
  })

  it('does not show mobile shell on desktop viewport', () => {
    window.matchMedia = vi.fn((query) => ({
      matches: query !== '(max-width: 767px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))

    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    expect(screen.queryByText('Fleet')).not.toBeInTheDocument()
  })

  // ── Issue #373 WAI-ARIA tablist a11y tests ─────────────────────────────────

  it('nav has role="tablist" with aria-label', () => {
    render(
      <MobileShell currentTab="fleet" onTabChange={vi.fn()}>
        <div>content</div>
      </MobileShell>
    )
    const tablist = screen.getByRole('tablist')
    expect(tablist).toBeInTheDocument()
    expect(tablist).toHaveAttribute('aria-label', 'Main navigation')
  })

  it('each tab button has role="tab" and aria-label', () => {
    render(
      <MobileShell currentTab="fleet" onTabChange={vi.fn()}>
        <div>content</div>
      </MobileShell>
    )
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(5)
    const labels = tabs.map((t) => t.getAttribute('aria-label'))
    expect(labels).toContain('Fleet')
    expect(labels).toContain('Maxwell')
  })

  it('active tab has aria-selected=true, others false', () => {
    render(
      <MobileShell currentTab="maxwell" onTabChange={vi.fn()}>
        <div>content</div>
      </MobileShell>
    )
    const tabs = screen.getAllByRole('tab')
    const selected = tabs.filter((t) => t.getAttribute('aria-selected') === 'true')
    const notSelected = tabs.filter((t) => t.getAttribute('aria-selected') === 'false')
    expect(selected).toHaveLength(1)
    expect(selected[0]).toHaveAttribute('aria-label', 'Maxwell')
    expect(notSelected).toHaveLength(4)
  })

  it('ArrowRight key moves to next tab', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>content</div>
      </MobileShell>
    )
    const fleetTab = screen.getByRole('tab', { name: 'Fleet' })
    fireEvent.keyDown(fleetTab, { key: 'ArrowRight' })
    expect(handleTabChange).toHaveBeenCalledWith('workflows')
  })

  it('ArrowLeft key wraps from first to last tab', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>content</div>
      </MobileShell>
    )
    const fleetTab = screen.getByRole('tab', { name: 'Fleet' })
    fireEvent.keyDown(fleetTab, { key: 'ArrowLeft' })
    expect(handleTabChange).toHaveBeenCalledWith('more')
  })

  it('all SVG icons have aria-hidden="true"', () => {
    render(
      <MobileShell currentTab="fleet" onTabChange={vi.fn()}>
        <div>content</div>
      </MobileShell>
    )
    const svgs = document.querySelectorAll('svg')
    svgs.forEach((svg) => {
      expect(svg.getAttribute('aria-hidden')).toBe('true')
    })
  })
})

// ── Test helper ───────────────────────────────────────────────────────────────
function Counter() {
  const [count, setCount] = React.useState(0)
  return (
    <div>
      <div>Count: {count}</div>
      <button onClick={() => setCount(count + 1)}>+</button>
    </div>
  )
}
