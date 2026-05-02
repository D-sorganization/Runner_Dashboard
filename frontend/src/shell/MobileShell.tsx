import React, { useState, useMemo, ReactNode, useCallback, useEffect, useRef } from 'react'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { colorTokens, spacingTokens, touchTokens } from '../design/tokens'
import { FloatingActionButton } from '../primitives/FloatingActionButton'
import { AgentDispatchPage } from '../pages/AgentDispatch'

type MainTabId = 'fleet' | 'workflows' | 'remediation' | 'maxwell' | 'more'
type DrawerTabId = 'org' | 'heavy' | 'assessments' | 'requests' | 'credentials' | 'reports' | 'health'
export type TabId = MainTabId | DrawerTabId

export interface MobileShellProps {
  children: ReactNode
  currentTab: TabId
  onTabChange: (tab: TabId) => void
  tabContent?: Record<TabId, ReactNode>
}

// Inline SVG icons — aria-hidden so screen readers skip them.
function FleetIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  )
}

function WorkflowsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 1v6m0 6v6m4.22-10.22l4.24-4.24M6.34 6.34L2.1 2.1m17.9 17.9l-4.24-4.24M6.34 17.66l-4.24 4.24" />
    </svg>
  )
}

function RemediationIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  )
}

function MaxwellIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <line x1="9" y1="21" x2="9" y2="9" />
    </svg>
  )
}

function MoreIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="5" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="12" cy="19" r="1" />
    </svg>
  )
}

const iconMap = {
  fleet: FleetIcon,
  workflows: WorkflowsIcon,
  remediation: RemediationIcon,
  maxwell: MaxwellIcon,
  more: MoreIcon,
}

// Tab configuration
const mainTabs: Array<{ id: MainTabId; label: string; Icon: typeof FleetIcon }> = [
  { id: 'fleet', label: 'Fleet', Icon: FleetIcon },
  { id: 'workflows', label: 'Workflows', Icon: WorkflowsIcon },
  { id: 'remediation', label: 'Remediation', Icon: RemediationIcon },
  { id: 'maxwell', label: 'Maxwell', Icon: MaxwellIcon },
  { id: 'more', label: 'More', Icon: MoreIcon },
]

// Additional tabs in drawer
const drawerTabs: Array<{ id: DrawerTabId; label: string }> = [
  { id: 'org', label: 'Org' },
  { id: 'heavy', label: 'Heavy Runners' },
  { id: 'assessments', label: 'Assessments' },
  { id: 'requests', label: 'Feature Requests' },
  { id: 'credentials', label: 'Credentials' },
  { id: 'reports', label: 'Reports' },
  { id: 'health', label: 'Queue Health' },
]

function tabIndexForId(tabId: MainTabId): number {
  return mainTabs.findIndex((t) => t.id === tabId)
}

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
  const showDispatchFab = !dispatchOpen && ['fleet', 'workflows', 'remediation', 'queue'].includes(currentTab)

  const tabRefs = useRef<Record<MainTabId, HTMLButtonElement | null>>({
    fleet: null,
    workflows: null,
    remediation: null,
    maxwell: null,
    more: null,
  })

  const handleTabClick = useCallback((tabId: MainTabId) => {
    onTabChange(tabId)
    if (tabId === 'more') {
      setDrawerOpen(true)
    }
  }, [onTabChange])

  const handleDrawerTabClick = useCallback((tabId: DrawerTabId, label: string) => {
    onTabChange(tabId)
    setDrawerAnnouncement(`${label} selected`)
    setDrawerOpen(false)
  }, [onTabChange])

  // Arrow-key cycling per WAI-ARIA tablist pattern
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLButtonElement>, tabId: MainTabId) => {
    const tabs = mainTabs.map((t) => t.id)
    const idx = tabs.indexOf(tabId)
    let nextIdx = idx

    switch (e.key) {
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault()
        nextIdx = idx === 0 ? tabs.length - 1 : idx - 1
        break
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault()
        nextIdx = idx === tabs.length - 1 ? 0 : idx + 1
        break
      case 'Home':
        e.preventDefault()
        nextIdx = 0
        break
      case 'End':
        e.preventDefault()
        nextIdx = tabs.length - 1
        break
      default:
        return
    }

    const nextTab = tabs[nextIdx]
    onTabChange(nextTab)
    tabRefs.current[nextTab]?.focus()
  }, [onTabChange])

  // Only show mobile shell on small viewports
  if (!isMobile) {
    return <>{children}</>
  }

  // Resolve native content for the active tab, if provided.
  const nativeContent = tabContent?.[currentTab]

  return (
    <div className="mobile-shell">
      {/* Main content area — native mobile component takes precedence when provided */}
      <div className="mobile-shell__content">
        {nativeContent != null ? (
          <>
            {/* Keep legacy App mounted but hidden so it keeps its internal state */}
            <div style={{ display: 'none' }} aria-hidden="true">{children}</div>
            {nativeContent}
          </>
        ) : (
          children
        )}
      </div>
      <div className="visually-hidden" aria-live="polite" aria-atomic="true">
        {drawerAnnouncement}
      </div>

      {/* Bottom Tab Bar — WAI-ARIA tablist */}
      <nav
        className="mobile-shell__nav"
        role="tablist"
        aria-label="Main navigation"
      >
        {mainTabs.map((tab) => {
          const isActive = currentTab === tab.id
          const Icon = tab.Icon
          return (
            <button
              key={tab.id}
              ref={(el) => { tabRefs.current[tab.id] = el }}
              onClick={() => handleTabClick(tab.id)}
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
              {drawerTabs.map((tab) => (
                <button
                  key={tab.id}
                  className="mobile-shell__drawer-item"
                  onClick={() => handleDrawerTabClick(tab.id, tab.label)}
                  type="button"
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
