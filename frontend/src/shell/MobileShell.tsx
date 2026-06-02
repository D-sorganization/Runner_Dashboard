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
import { AgentDispatchPage } from '../pages/AgentDispatch'
import {
  mobilePrimaryItems,
  mobileDrawerItems,
  type NavItem,
} from './navRegistry'

/** A mobile tab is identified by the registry tabId (legacy App tab string). */
export type TabId = string

export interface MobileShellProps {
  children: ReactNode
  currentTab: TabId
  onTabChange: (tab: TabId) => void
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
  label: it.label,
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

export function MobileShell({ children, currentTab, onTabChange, tabContent }: MobileShellProps) {
  const breakpoint = useBreakpoint()
  const isMobile = breakpoint !== 'lg' && breakpoint !== 'xl'
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [dispatchOpen, setDispatchOpen] = useState(false)
  const [drawerAnnouncement, setDrawerAnnouncement] = useState('')

  const openDispatch = useCallback(() => setDispatchOpen(true), [])
  const closeDispatch = useCallback(() => setDispatchOpen(false), [])

  // Close dispatch sheet on Escape
  useEffect(() => {
    if (!dispatchOpen) return
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        closeDispatch()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [dispatchOpen, closeDispatch])

  // Determine whether to show the FAB.
  // Visible on Fleet, Workflows, Remediation, Queue. Hidden on AgentDispatch itself.
  const showDispatchFab = !dispatchOpen && FAB_TABS.has(currentTab)

  const tabRefs = useRef<Record<TabId, HTMLButtonElement | null>>({})

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
      </header>
      {/* Main content area — native mobile component takes precedence when provided */}
      <main id="main-content" className="mobile-shell__content" role="main" tabIndex={-1}>
        {nativeContent != null ? (
          <>
            {/* Keep legacy App mounted but hidden so it keeps its internal state */}
            <div style={{ display: 'none' }} aria-hidden="true">{children}</div>
            {nativeContent}
          </>
        ) : (
          children
        )}
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

      {/* Floating Action Button — Quick dispatch agent */}
      <FloatingActionButton
        aria-label="Quick dispatch agent"
        visible={showDispatchFab}
        onClick={openDispatch}
        data-testid="dispatch-fab"
      />

      {/* Agent Dispatch Modal Sheet */}
      {dispatchOpen && (
        <div className="mobile-shell__sheet-overlay" onClick={closeDispatch} role="presentation">
          <div
            className="mobile-shell__sheet"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Agent dispatch"
          >
            <div className="mobile-shell__sheet-header">
              <h2 className="mobile-shell__sheet-title">Quick Dispatch</h2>
              <button
                className="mobile-shell__sheet-close"
                onClick={closeDispatch}
                type="button"
                aria-label="Close dispatch sheet"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="20" height="20">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            <div className="mobile-shell__sheet-body">
              <AgentDispatchPage />
            </div>
          </div>
        </div>
      )}

      {/* Drawer for additional tabs */}
      {drawerOpen && (
        <div className="mobile-shell__drawer-overlay" onClick={() => setDrawerOpen(false)} role="presentation">
          <div className="mobile-shell__drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="More options">
            <div className="mobile-shell__drawer-header">
              <button
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
