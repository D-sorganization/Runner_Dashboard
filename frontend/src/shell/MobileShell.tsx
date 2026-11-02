/**
 * MobileShell.tsx — the mobile application shell (issue #821, part of #818).
 *
 * Driven entirely by the single nav registry (DRY): the bottom tab bar renders
 * `mobilePrimary` items, and the "More" drawer renders `mobileDrawer` items.
 * This replaces the previously hand-synced 12-entry list that had drifted from
 * the 24-tab desktop registry, restoring ~13 missing mobile features — most
 * importantly the Conductor pause/drain/budget controls and AgentDispatch as a
 * first-class drawer entry that an on-call operator needs on a phone.
 *
 * Tab identity is the registry `tabId` (the legacy App tab string), so the
 * mobile shell and the legacy App stay in lockstep without a translation table.
 *
 * LoD: flat typed props only — `currentTab`, `onTabChange(tabId)`, optional
 * `tabContent` keyed by tabId. Orthogonality: a pure presentational nav that
 * owns no page state.
 */
import React, { useState, ReactNode, useCallback, useEffect, useRef } from 'react'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { FloatingActionButton } from '../primitives/FloatingActionButton'
import { AskSheet } from './AskSheet'

const LazyBarbSidebar = React.lazy(() =>
  import('./BarbSidebar').then(({ BarbSidebar }) => ({ default: BarbSidebar })),
)
import {
  mobilePrimaryItems,
  mobileDrawerItems,
  navItemById,
  type NavItem,
} from './navRegistry'

/** A mobile tab is identified by the registry tabId (legacy App tab string). */
export type TabId = string

export interface MobileShellProps {
  children?: ReactNode
  currentTab: TabId
  onTabChange: (tab: TabId) => void
  onThreadSelected?: (threadId: string, role: string) => void
  tabContent?: Record<TabId, ReactNode>
}

// "More" pseudo-tab id — the bottom bar's final slot opens the drawer rather
// than navigating to a page.
const MORE_TAB_ID = '__more__'

function MoreIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="5" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="12" cy="19" r="1" />
    </svg>
  )
}

// Compact chat-bubble glyph for the header's icon-only Ask Barb trigger
// (issue #3749 — a labeled text pill reserved a full header row).
function AskBarbIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </svg>
  )
}

// Tab bar entries: the registry's mobilePrimary items plus the trailing "More"
// trigger. Derived once from the single source of truth (DRY).
interface BottomTab {
  id: TabId
  label: string
  Icon: NavItem['Icon']
  isMore: boolean
}

const PRIMARY_TABS: BottomTab[] = mobilePrimaryItems().map((it) => ({
  id: it.tabId,
  label: it.mobileLabel || it.label,
  Icon: it.Icon,
  isMore: false,
}))

const MAIN_TABS: BottomTab[] = [
  ...PRIMARY_TABS,
  { id: MORE_TAB_ID, label: 'More', Icon: MoreIcon, isMore: true },
]

const DRAWER_ITEMS = mobileDrawerItems()

// FAB visibility: the operator-action surfaces where a quick agent dispatch
// makes sense. Keyed on registry tabIds.
const FAB_TABS = new Set<TabId>(['overview', 'workflows', 'remediation', 'queue'])

export function MobileShell({ children, currentTab, onTabChange, onThreadSelected, tabContent }: MobileShellProps) {
  const breakpoint = useBreakpoint()
  const isMobile = breakpoint !== 'lg' && breakpoint !== 'xl'
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [askOpen, setAskOpen] = useState(false)
  const [barbMounted, setBarbMounted] = useState(false)
  const [barbOpen, setBarbOpen] = useState(false)
  const barbToggleRef = useRef<HTMLButtonElement>(null)
  const [drawerAnnouncement, setDrawerAnnouncement] = useState('')
  const drawerRef = useRef<HTMLDivElement>(null)
  const drawerCloseRef = useRef<HTMLButtonElement>(null)
  const drawerWasOpenRef = useRef(false)

  const openAsk = useCallback(() => setAskOpen(true), [])
  const closeAsk = useCallback(() => setAskOpen(false), [])
  const handleBarbNavigate = useCallback((tabId: TabId) => {
    onTabChange(tabId)
    setBarbOpen(false)
  }, [onTabChange])

  // Approved assistant navigation changes the route outside this shell's callbacks.
  // Dismiss the mobile modal only for registered routes so the destination is usable.
  useEffect(() => {
    if (!isMobile || !barbOpen) return
    const handleAssistantNavigation = (event: Event) => {
      const tabId = (event as CustomEvent<{ tabId?: unknown }>).detail?.tabId
      if (typeof tabId === 'string' && navItemById(tabId)) setBarbOpen(false)
    }
    window.addEventListener('runner-dashboard:navigate', handleAssistantNavigation)
    return () => window.removeEventListener('runner-dashboard:navigate', handleAssistantNavigation)
  }, [barbOpen, isMobile])

  // Close ask sheet on Escape
  useEffect(() => {
    if (!askOpen) return
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        closeAsk()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [askOpen, closeAsk])

  // Determine whether to show the FAB.
  // Visible on Fleet, Workflows, Remediation, Queue. Hidden on Staff itself.
  const isFabTab = FAB_TABS.has(currentTab)
  const showAskFab = !askOpen && isFabTab

  const tabRefs = useRef<Record<TabId, HTMLButtonElement | null>>({})

  useEffect(() => {
    if (!drawerOpen) {
      if (drawerWasOpenRef.current) tabRefs.current[MORE_TAB_ID]?.focus()
      drawerWasOpenRef.current = false
      return
    }

    drawerWasOpenRef.current = true
    drawerCloseRef.current?.focus()

    const handleDrawerKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setDrawerOpen(false)
        return
      }
      if (event.key !== 'Tab') return

      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )
      if (!focusable?.length) return

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (!drawerRef.current?.contains(active)) {
        event.preventDefault()
        const target = event.shiftKey ? last : first
        target.focus()
      } else if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleDrawerKeyDown)
    return () => document.removeEventListener('keydown', handleDrawerKeyDown)
  }, [drawerOpen])

  const handleTabClick = useCallback((tab: BottomTab) => {
    if (tab.isMore) {
      setDrawerOpen(true)
      return
    }
    onTabChange(tab.id)
  }, [onTabChange])

  const handleDrawerTabClick = useCallback((tabId: TabId, label: string) => {
    onTabChange(tabId)
    setDrawerAnnouncement(`${label} selected`)
    setDrawerOpen(false)
  }, [onTabChange])

  // Arrow-key cycling per WAI-ARIA tablist pattern
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLButtonElement>, tabId: TabId) => {
    const ids = MAIN_TABS.map((t) => t.id)
    const idx = ids.indexOf(tabId)
    let nextIdx = idx

    switch (e.key) {
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault()
        nextIdx = idx === 0 ? ids.length - 1 : idx - 1
        break
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault()
        nextIdx = idx === ids.length - 1 ? 0 : idx + 1
        break
      case 'Home':
        e.preventDefault()
        nextIdx = 0
        break
      case 'End':
        e.preventDefault()
        nextIdx = ids.length - 1
        break
      default:
        return
    }

    const nextTab = MAIN_TABS[nextIdx]
    if (!nextTab.isMore) onTabChange(nextTab.id)
    tabRefs.current[nextTab.id]?.focus()
  }, [onTabChange])

  // Only show mobile shell on small viewports
  if (!isMobile) {
    return <>{children}</>
  }

  // Resolve native content for the active tab, if provided.
  const nativeContent = tabContent?.[currentTab]

  // The active bottom tab is whichever primary tab matches; otherwise the
  // selection lives in the drawer, so the "More" trigger carries the active cue.
  const activeIsPrimary = PRIMARY_TABS.some((t) => t.id === currentTab)

  return (
    <div className="mobile-shell">
      <header className="mobile-shell__header" role="banner">
        <a className="skip-link" href="#main-content">
          Skip to main content
        </a>
        <button
          ref={barbToggleRef}
          type="button"
          className="mobile-shell__ask-barb"
          aria-label="Ask Barb"
          aria-controls="barb-sidebar"
          aria-expanded={barbOpen}
          data-testid="mobile-ask-barb"
          onClick={() => {
            setBarbMounted(true)
            setBarbOpen((open) => !open)
          }}
        >
          <AskBarbIcon className="mobile-shell__ask-barb-icon" />
        </button>
      </header>
      {/* Main content area — native mobile component takes precedence when provided.
          On FAB tabs, reserve room so the last card isn't hidden under the FAB (#3751). */}
      <main
        id="main-content"
        className={`mobile-shell__content${isFabTab ? ' mobile-shell__content--fab-clearance' : ''}`}
        role="main"
        tabIndex={-1}
      >
        {nativeContent ?? children}
      </main>
      <div className="visually-hidden" aria-live="polite" aria-atomic="true">
        {drawerAnnouncement}
      </div>

      {/* Bottom Tab Bar — WAI-ARIA tablist */}
      <nav
        className="mobile-shell__nav"
        role="navigation"
        aria-label="Main navigation"
      >
        <div className="mobile-shell__tablist" role="tablist" aria-label="Main navigation">
          {MAIN_TABS.map((tab) => {
            const isActive = tab.isMore ? !activeIsPrimary : currentTab === tab.id
            const Icon = tab.Icon
            return (
              <button
                key={tab.id}
                ref={(el) => { tabRefs.current[tab.id] = el }}
                onClick={() => handleTabClick(tab)}
                onKeyDown={(e) => handleKeyDown(e, tab.id)}
                className={`mobile-shell__tab ${isActive ? 'mobile-shell__tab--active' : ''}`}
                role="tab"
                aria-selected={isActive}
                tabIndex={isActive ? 0 : -1}
                title={tab.label}
                type="button"
              >
                <span className="mobile-shell__tab-accent" aria-hidden="true" />
                <Icon className="mobile-shell__tab-icon" />
                <span className="mobile-shell__tab-label">{tab.label}</span>
              </button>
            )
          })}
        </div>
      </nav>

      {/* Floating Action Button — Ask staff */}
      <FloatingActionButton
        aria-label="Ask"
        visible={showAskFab}
        onClick={openAsk}
        data-testid="ask-fab"
      />

      {/* Ask Modal Sheet */}
      <AskSheet
        isOpen={askOpen}
        onClose={closeAsk}
        onOpenStaffConsole={() => onTabChange('staff')}
      />
      {barbMounted ? (
        <React.Suspense fallback={null}>
          <LazyBarbSidebar
            open={barbOpen}
            mobile
            onClose={() => setBarbOpen(false)}
            onNavigate={handleBarbNavigate}
            onThreadSelected={onThreadSelected}
            restoreFocusTargetRef={barbToggleRef}
          />
        </React.Suspense>
      ) : null}

      {/* Drawer for additional tabs */}
      {drawerOpen && (
        <div className="mobile-shell__drawer-overlay" onClick={() => setDrawerOpen(false)} role="presentation">
          <div ref={drawerRef} className="mobile-shell__drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="More options">
            <div className="mobile-shell__drawer-header">
              <button
                ref={drawerCloseRef}
                className="mobile-shell__drawer-close"
                onClick={() => setDrawerOpen(false)}
                type="button"
                aria-label="Close drawer"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="20" height="20">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            <div className="mobile-shell__drawer-content">
              {DRAWER_ITEMS.map((item) => {
                const Icon = item.Icon
                const isActive = currentTab === item.tabId
                return (
                  <button
                    key={item.tabId}
                    className={`mobile-shell__drawer-item ${isActive ? 'mobile-shell__drawer-item--active' : ''}`}
                    onClick={() => handleDrawerTabClick(item.tabId, item.label)}
                    aria-current={isActive ? 'page' : undefined}
                    title={item.tooltip}
                    type="button"
                  >
                    <Icon className="mobile-shell__drawer-item-icon" />
                    <span>{item.label}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
