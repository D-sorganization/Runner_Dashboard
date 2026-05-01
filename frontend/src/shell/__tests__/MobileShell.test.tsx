import React from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MobileShell } from '../MobileShell'

describe('MobileShell', () => {
  beforeEach(() => {
    // Mock window.matchMedia for viewport detection
    window.matchMedia = vi.fn((query) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    } as MediaQueryList))
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

  it('uses SVG icons instead of emoji', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    // Each tab should have an SVG icon with aria-hidden="true"
    const svgs = screen.getAllByRole('tab').map((tab) =>
      tab.querySelector('svg[aria-hidden="true"]')
    )
    expect(svgs.every((svg) => svg !== null)).toBe(true)
  })

  it('exposes role=tablist and role=tab semantics', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="workflows" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tablist = screen.getByRole('tablist')
    expect(tablist).toBeInTheDocument()
    expect(tablist).toHaveAttribute('aria-label', 'Main navigation')

    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(5)
  })

  it('sets aria-selected on active tab only', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="workflows" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    tabs.forEach((tab) => {
      const label = tab.querySelector('.mobile-shell__tab-label')?.textContent
      const isSelected = tab.getAttribute('aria-selected') === 'true'
      if (label === 'Workflows') {
        expect(isSelected).toBe(true)
      } else {
        expect(isSelected).toBe(false)
      }
    })
  })

  it('sets tabIndex=0 on active tab and -1 on inactive tabs', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    const fleetTab = tabs.find((t) => t.textContent?.includes('Fleet'))
    const otherTabs = tabs.filter((t) => !t.textContent?.includes('Fleet'))

    expect(fleetTab).toHaveAttribute('tabIndex', '0')
    otherTabs.forEach((tab) => {
      expect(tab).toHaveAttribute('tabIndex', '-1')
    })
  })

  it('renders 2px top accent bar for color-blind active cue', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="workflows" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const activeTab = screen.getAllByRole('tab').find(
      (t) => t.getAttribute('aria-selected') === 'true'
    )
    expect(activeTab).toBeTruthy()

    const accent = activeTab!.querySelector('.mobile-shell__tab-accent')
    expect(accent).toBeInTheDocument()
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

  it('cycles focus with ArrowRight keyboard', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    const fleetTab = tabs.find((t) => t.textContent?.includes('Fleet'))!

    fireEvent.keyDown(fleetTab, { key: 'ArrowRight' })

    expect(handleTabChange).toHaveBeenCalledWith('workflows')
  })

  it('cycles focus with ArrowLeft keyboard wrapping to end', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    const fleetTab = tabs.find((t) => t.textContent?.includes('Fleet'))!

    fireEvent.keyDown(fleetTab, { key: 'ArrowLeft' })

    expect(handleTabChange).toHaveBeenCalledWith('more')
  })

  it('cycles to first tab with Home key', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="maxwell" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    const maxwellTab = tabs.find((t) => t.textContent?.includes('Maxwell'))!

    fireEvent.keyDown(maxwellTab, { key: 'Home' })

    expect(handleTabChange).toHaveBeenCalledWith('fleet')
  })

  it('cycles to last tab with End key', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    const fleetTab = tabs.find((t) => t.textContent?.includes('Fleet'))!

    fireEvent.keyDown(fleetTab, { key: 'End' })

    expect(handleTabChange).toHaveBeenCalledWith('more')
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

    // Open drawer
    const moreTab = screen.getByText('More')
    fireEvent.click(moreTab)

    await waitFor(() => {
      expect(screen.getByText('Org')).toBeInTheDocument()
    })

    // Click backdrop
    const overlay = container.querySelector('.mobile-shell__drawer-overlay')
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

    // Increment counter
    const incrementBtn = screen.getByText('+')
    fireEvent.click(incrementBtn)
    fireEvent.click(incrementBtn)

    expect(screen.getByText('Count: 2')).toBeInTheDocument()

    // Switch to different tab
    const workflowsTab = screen.getByText('Workflows')
    fireEvent.click(workflowsTab)

    // Re-render with same component mounted
    rerender(
      <MobileShell currentTab="workflows" onTabChange={handleTabChange}>
        <Counter />
      </MobileShell>
    )

    // State should be preserved
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
    }) as MediaQueryList)

    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab="fleet" onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    // Mobile nav should not be present on desktop
    expect(screen.queryByText('Fleet')).not.toBeInTheDocument()
  })
})

// Test helper component
function Counter() {
  const [count, setCount] = React.useState(0)
  return (
    <div>
      <div>Count: {count}</div>
      <button onClick={() => setCount(count + 1)}>+</button>
    </div>
  )
}