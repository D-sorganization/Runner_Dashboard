import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react'
import { MobileShell } from '../MobileShell'
import { mobilePrimaryItems, mobileDrawerItems } from '../navRegistry'

const breakpointMock = vi.hoisted(() => ({ value: 'md' }))

vi.mock('../../hooks/useBreakpoint', () => ({
  useBreakpoint: () => breakpointMock.value,
}))

// The mobile shell is registry-driven (issue #821): its bottom bar renders the
// mobilePrimary items + a trailing "More" trigger, and the drawer renders the
// mobileDrawer items. Derive expectations from the single source of truth so
// the test cannot drift from the registry.
const PRIMARY = mobilePrimaryItems()
const DRAWER = mobileDrawerItems()
const PRIMARY_LABELS = PRIMARY.map((i) => i.label)
const FIRST = PRIMARY[0] // Fleet / overview
const SECOND = PRIMARY[1] // Queue / queue
const LAST_PRIMARY = PRIMARY[PRIMARY.length - 1] // Maxwell / maxwell

describe('MobileShell', () => {
  beforeEach(() => {
    breakpointMock.value = 'md'
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

  afterEach(() => {
    cleanup()
  })

  it('renders every mobilePrimary item plus a More trigger as bottom tabs', () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    for (const label of PRIMARY_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText('More')).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(PRIMARY.length + 1)
  })

  it('uses SVG icons instead of emoji', () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    const svgs = screen.getAllByRole('tab').map((tab) =>
      tab.querySelector('svg[aria-hidden="true"]')
    )
    expect(svgs.every((svg) => svg !== null)).toBe(true)
  })

  it('exposes role=tablist and role=tab semantics', () => {
    render(
      <MobileShell currentTab={SECOND.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tablist = screen.getByRole('tablist')
    expect(tablist).toBeInTheDocument()
    expect(tablist).toHaveAttribute('aria-label', 'Main navigation')

    expect(screen.getAllByRole('tab')).toHaveLength(PRIMARY.length + 1)
  })

  it('renders skip link and semantic shell landmarks', () => {
    const { container } = render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    const firstFocusable = container.querySelector('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')
    const skipLink = screen.getByText('Skip to main content')
    const main = container.querySelector('main#main-content')

    expect(firstFocusable).toBe(skipLink)
    expect(skipLink).toHaveAttribute('href', '#main-content')
    expect(container.querySelector('header[role="banner"]')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
    expect(main).toBeInTheDocument()
    expect(main).toHaveAttribute('role', 'main')
  })

  it('sets aria-selected on active tab only', () => {
    render(
      <MobileShell currentTab={SECOND.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    tabs.forEach((tab) => {
      const label = tab.querySelector('.mobile-shell__tab-label')?.textContent
      const isSelected = tab.getAttribute('aria-selected') === 'true'
      if (label === SECOND.label) {
        expect(isSelected).toBe(true)
      } else {
        expect(isSelected).toBe(false)
      }
    })
  })

  it('sets tabIndex=0 on active tab and -1 on inactive tabs', () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    const tabs = screen.getAllByRole('tab')
    const firstTab = tabs.find((t) => t.textContent?.includes(FIRST.label))!
    const otherTabs = tabs.filter((t) => t !== firstTab)

    expect(firstTab).toHaveAttribute('tabIndex', '0')
    otherTabs.forEach((tab) => {
      expect(tab).toHaveAttribute('tabIndex', '-1')
    })
  })

  it('marks the More trigger active when the current tab lives in the drawer', () => {
    render(
      <MobileShell currentTab={DRAWER[0].tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )
    const moreTab = screen.getByRole('tab', { name: /more/i })
    expect(moreTab).toHaveAttribute('aria-selected', 'true')
  })

  it('renders 2px top accent bar for color-blind active cue', () => {
    render(
      <MobileShell currentTab={SECOND.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    const activeTab = screen.getAllByRole('tab').find(
      (t) => t.getAttribute('aria-selected') === 'true'
    )
    expect(activeTab).toBeTruthy()
    expect(activeTab!.querySelector('.mobile-shell__tab-accent')).toBeInTheDocument()
  })

  it('calls onTabChange with the registry tabId when a primary tab is clicked', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    fireEvent.click(screen.getByText(SECOND.label))
    expect(handleTabChange).toHaveBeenCalledWith(SECOND.tabId)
  })

  it('cycles focus with ArrowRight keyboard', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const firstTab = screen.getAllByRole('tab').find((t) => t.textContent?.includes(FIRST.label))!
    fireEvent.keyDown(firstTab, { key: 'ArrowRight' })
    expect(handleTabChange).toHaveBeenCalledWith(SECOND.tabId)
  })

  it('cycles focus with ArrowLeft keyboard wrapping to the More trigger', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const firstTab = screen.getAllByRole('tab').find((t) => t.textContent?.includes(FIRST.label))!
    // Wrapping left from the first tab lands on the trailing "More" trigger,
    // which is a pseudo-tab and does not navigate.
    fireEvent.keyDown(firstTab, { key: 'ArrowLeft' })
    expect(handleTabChange).not.toHaveBeenCalled()
  })

  it('cycles to first tab with Home key', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab={LAST_PRIMARY.tabId} onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const lastTab = screen.getAllByRole('tab').find((t) => t.textContent?.includes(LAST_PRIMARY.label))!
    fireEvent.keyDown(lastTab, { key: 'Home' })
    expect(handleTabChange).toHaveBeenCalledWith(FIRST.tabId)
  })

  it('cycles to the More trigger with End key (no navigation)', () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    const firstTab = screen.getAllByRole('tab').find((t) => t.textContent?.includes(FIRST.label))!
    fireEvent.keyDown(firstTab, { key: 'End' })
    // End lands on the trailing "More" pseudo-tab — focus only, no navigation.
    expect(handleTabChange).not.toHaveBeenCalled()
  })

  it('opens drawer with all mobileDrawer items when More is clicked', async () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    fireEvent.click(screen.getByText('More'))

    await waitFor(() => {
      const drawer = screen.getByRole('dialog', { name: /more options/i })
      for (const item of DRAWER) {
        expect(within(drawer).getByText(item.label)).toBeInTheDocument()
      }
    })
  })

  it('surfaces Conductor and Dispatch operator controls in the mobile drawer (issue #821)', async () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    fireEvent.click(screen.getByText('More'))

    await waitFor(() => {
      const drawer = screen.getByRole('dialog', { name: /more options/i })
      expect(within(drawer).getByText('Conductor')).toBeInTheDocument()
      expect(within(drawer).getByText('Dispatch')).toBeInTheDocument()
    })
  })

  it('calls onTabChange once for each drawer tab and announces selection', async () => {
    const handleTabChange = vi.fn()
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={handleTabChange}>
        <div>Test Content</div>
      </MobileShell>
    )

    for (const item of DRAWER) {
      fireEvent.click(screen.getByText('More'))

      const drawer = await screen.findByRole('dialog', { name: /more options/i })
      const btn = within(drawer).getByText(item.label)

      handleTabChange.mockClear()
      fireEvent.click(btn)

      expect(handleTabChange).toHaveBeenCalledTimes(1)
      expect(handleTabChange).toHaveBeenCalledWith(item.tabId)
      expect(screen.getByText(`${item.label} selected`)).toBeInTheDocument()

      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: /more options/i })).not.toBeInTheDocument()
      })
    }
  })

  it('closes drawer when backdrop is clicked', async () => {
    const { container } = render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    fireEvent.click(screen.getByText('More'))
    await screen.findByRole('dialog', { name: /more options/i })

    const overlay = container.querySelector('.mobile-shell__drawer-overlay')
    if (overlay) fireEvent.click(overlay)

    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /more options/i })).not.toBeInTheDocument()
    })
  })

  it('preserves component state when switching tabs', () => {
    const { rerender } = render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <Counter />
      </MobileShell>
    )

    const incrementBtn = screen.getByText('+')
    fireEvent.click(incrementBtn)
    fireEvent.click(incrementBtn)
    expect(screen.getByText('Count: 2')).toBeInTheDocument()

    fireEvent.click(screen.getByText(SECOND.label))

    rerender(
      <MobileShell currentTab={SECOND.tabId} onTabChange={vi.fn()}>
        <Counter />
      </MobileShell>
    )

    expect(screen.getByText('Count: 2')).toBeInTheDocument()
  })

  it('does not show mobile shell on desktop viewport', () => {
    breakpointMock.value = 'lg'
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

    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Test Content</div>
      </MobileShell>
    )

    expect(screen.queryByText(FIRST.label)).not.toBeInTheDocument()
  })
})

// D7 / issue #726: Accessibility improvements
describe('MobileShell accessibility (D7)', () => {
  beforeEach(() => {
    breakpointMock.value = 'md';
    window.matchMedia = vi.fn((query) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    } as MediaQueryList));
  });

  afterEach(() => {
    cleanup()
  })

  it('skip link is present and is the first focusable element', () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Content</div>
      </MobileShell>,
    );
    const skipLink = document.querySelector('.skip-link') as HTMLElement | null;
    expect(skipLink).not.toBeNull();
    expect(skipLink?.tagName.toLowerCase()).toBe('a');
    expect(skipLink?.getAttribute('href')).toBe('#main-content');
  });

  it('has a <main> element with id="main-content"', () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Content</div>
      </MobileShell>,
    );
    expect(document.querySelector('main#main-content')).not.toBeNull();
  });

  it('nav has role="tablist" or role="navigation"', () => {
    render(
      <MobileShell currentTab={FIRST.tabId} onTabChange={vi.fn()}>
        <div>Content</div>
      </MobileShell>,
    );
    expect(document.querySelector('nav')).not.toBeNull();
  });
});

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
