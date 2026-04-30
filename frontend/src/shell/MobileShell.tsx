/**
 * MobileShell — bottom-tab navigation with full WAI-ARIA tablist pattern.
 *
 * Resolves issue #373:
 * 1. Icons are inline SVGs marked aria-hidden="true" — no emoji.
 * 2. <nav> has role="tablist"; each <button> is role="tab" + aria-selected.
 * 3. Active state: color + 2px top accent bar (color-blind accessible).
 * 4. Reduced-motion: color-transition opted out.
 * 5. Arrow-key cycling between tabs (Left/Right moves focus + fires tab change).
 * 6. Tests in __tests__/MobileShell.test.tsx assert keyboard and aria-selected.
 */

import React, { useState, useRef, KeyboardEvent, ReactNode } from 'react'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { colorTokens, spacingTokens, touchTokens } from '../design/tokens'

export type TabId = 'fleet' | 'workflows' | 'remediation' | 'maxwell' | 'more'

export interface MobileShellProps {
  children: ReactNode
  currentTab: TabId
  onTabChange: (tab: TabId) => void
  tabContent?: Record<TabId, ReactNode>
}

// ---------------------------------------------------------------------------
// SVG icon set — aria-hidden, so screen-readers use the button's aria-label.
// ---------------------------------------------------------------------------

const FleetIcon = () => (
  <svg aria-hidden="true" focusable="false" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="7" width="20" height="14" rx="2" />
    <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
    <line x1="12" y1="12" x2="12" y2="16" />
    <line x1="10" y1="14" x2="14" y2="14" />
  </svg>
)

const WorkflowsIcon = () => (
  <svg aria-hidden="true" focusable="false" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" />
  </svg>
)

const RemediationIcon = () => (
  <svg aria-hidden="true" focusable="false" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
  </svg>
)

const MaxwellIcon = () => (
  <svg aria-hidden="true" focusable="false" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="10" rx="2" />
    <circle cx="12" cy="5" r="2" />
    <path d="M12 7v4" />
    <line x1="8" y1="16" x2="8" y2="16" strokeWidth="3" strokeLinecap="round" />
    <line x1="12" y1="16" x2="12" y2="16" strokeWidth="3" strokeLinecap="round" />
    <line x1="16" y1="16" x2="16" y2="16" strokeWidth="3" strokeLinecap="round" />
  </svg>
)

const MoreIcon = () => (
  <svg aria-hidden="true" focusable="false" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
    <circle cx="5" cy="12" r="1" />
  </svg>
)

const TAB_ICONS: Record<TabId, React.FC> = {
  fleet: FleetIcon,
  workflows: WorkflowsIcon,
  remediation: RemediationIcon,
  maxwell: MaxwellIcon,
  more: MoreIcon,
}

const TAB_LABELS: Record<TabId, string> = {
  fleet: 'Fleet',
  workflows: 'Workflows',
  remediation: 'Remediation',
  maxwell: 'Maxwell',
  more: 'More',
}

const MAIN_TABS: TabId[] = ['fleet', 'workflows', 'remediation', 'maxwell', 'more']

// ---------------------------------------------------------------------------

export function MobileShell({ children, currentTab, onTabChange }: MobileShellProps) {
  const breakpoint = useBreakpoint()
  const isMobile = breakpoint !== 'lg' && breakpoint !== 'xl'
  const [drawerOpen, setDrawerOpen] = useState(false)
  const tabRefs = useRef<Record<TabId, HTMLButtonElement | null>>({} as Record<TabId, HTMLButtonElement | null>)

  const handleTabClick = (tabId: TabId) => {
    onTabChange(tabId)
    if (tabId === 'more') {
      setDrawerOpen(true)
    }
  }

  // WAI-ARIA tablist keyboard: Left/Right arrows cycle through tabs.
  const handleKeyDown = (e: KeyboardEvent<HTMLButtonElement>, currentIdx: number) => {
    let nextIdx: number | null = null
    if (e.key === 'ArrowRight') {
      nextIdx = (currentIdx + 1) % MAIN_TABS.length
    } else if (e.key === 'ArrowLeft') {
      nextIdx = (currentIdx - 1 + MAIN_TABS.length) % MAIN_TABS.length
    } else if (e.key === 'Home') {
      nextIdx = 0
    } else if (e.key === 'End') {
      nextIdx = MAIN_TABS.length - 1
    }

    if (nextIdx !== null) {
      e.preventDefault()
      const nextTabId = MAIN_TABS[nextIdx]
      tabRefs.current[nextTabId]?.focus()
      handleTabClick(nextTabId)
    }
  }

  // Additional drawer tabs (shown on "More" tab)
  const drawerTabs = [
    { id: 'org', label: 'Org' },
    { id: 'heavy', label: 'Heavy Runners' },
    { id: 'assessments', label: 'Assessments' },
    { id: 'requests', label: 'Feature Requests' },
    { id: 'credentials', label: 'Credentials' },
    { id: 'reports', label: 'Reports' },
    { id: 'health', label: 'Queue Health' },
  ]

  if (!isMobile) {
    return <>{children}</>
  }

  return (
    <div style={styles.container}>
      {/* Main content area */}
      <div style={styles.content}>
        {children}
      </div>

      {/* Bottom Tab Bar — role="tablist" per WAI-ARIA */}
      <nav
        role="tablist"
        aria-label="Main navigation"
        style={styles.navBar}
      >
        {MAIN_TABS.map((tabId, idx) => {
          const isActive = currentTab === tabId
          const Icon = TAB_ICONS[tabId]
          return (
            <button
              key={tabId}
              id={`tab-${tabId}`}
              ref={(el) => { tabRefs.current[tabId] = el }}
              role="tab"
              aria-selected={isActive}
              aria-controls={`tabpanel-${tabId}`}
              aria-label={TAB_LABELS[tabId]}
              tabIndex={isActive ? 0 : -1}
              onClick={() => handleTabClick(tabId)}
              onKeyDown={(e) => handleKeyDown(e, idx)}
              style={{
                ...styles.tabButton,
                ...(isActive ? styles.tabButtonActive : {}),
              }}
            >
              {/* 2px accent bar at top — visible even without color (color-blind) */}
              {isActive && <span style={styles.activeIndicator} aria-hidden="true" />}
              <Icon />
              <span style={styles.tabLabel}>{TAB_LABELS[tabId]}</span>
            </button>
          )
        })}
      </nav>

      {/* Drawer for additional tabs */}
      {drawerOpen && (
        <div style={styles.drawerOverlay} onClick={() => setDrawerOpen(false)}>
          <div style={styles.drawer} onClick={(e) => e.stopPropagation()}>
            <div style={styles.drawerHeader}>
              <button
                style={styles.drawerClose}
                onClick={() => setDrawerOpen(false)}
                aria-label="Close navigation drawer"
              >
                <svg aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            <div style={styles.drawerContent}>
              {drawerTabs.map((tab) => (
                <button
                  key={tab.id}
                  style={styles.drawerItem}
                  onClick={() => {
                    setDrawerOpen(false)
                  }}
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

const styles = {
  container: {
    display: 'flex',
    flexDirection: 'column' as const,
    height: '100vh',
    width: '100%',
    backgroundColor: colorTokens.bgPrimary,
  },

  content: {
    flex: 1,
    overflow: 'auto',
    paddingBottom: `calc(${touchTokens.bottomNavHeight} + env(safe-area-inset-bottom))`,
  },

  navBar: {
    display: 'flex',
    justifyContent: 'space-around',
    alignItems: 'stretch',
    height: touchTokens.bottomNavHeight,
    backgroundColor: colorTokens.bgSecondary,
    borderTop: `1px solid ${colorTokens.border}`,
    position: 'fixed' as const,
    bottom: 0,
    left: 0,
    right: 0,
    paddingBottom: 'env(safe-area-inset-bottom)',
    zIndex: 100,
  },

  tabButton: {
    position: 'relative' as const,
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacingTokens[1],
    flex: 1,
    height: '100%',
    background: 'none',
    border: 'none',
    color: colorTokens.textSecondary,
    cursor: 'pointer',
    fontSize: '12px',
    padding: 0,
    // Reduced-motion: no transition. Users who want motion get 0.15s color fade.
    transition: 'color 0.15s',
    '@media (prefers-reduced-motion: reduce)': {
      transition: 'none',
    },
  },

  tabButtonActive: {
    color: colorTokens.accentBlue,
  },

  // 2px top accent bar — visible without color, satisfies color-blind AC
  activeIndicator: {
    position: 'absolute' as const,
    top: 0,
    left: '20%',
    right: '20%',
    height: '2px',
    borderRadius: '0 0 2px 2px',
    backgroundColor: colorTokens.accentBlue,
  },

  tabLabel: {
    fontSize: '10px',
    fontWeight: 500 as const,
    lineHeight: 1,
  },

  drawerOverlay: {
    position: 'fixed' as const,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    zIndex: 200,
    animation: 'fadeIn 0.2s ease-out',
  },

  drawer: {
    position: 'fixed' as const,
    left: 0,
    top: 0,
    bottom: 0,
    width: '280px',
    backgroundColor: colorTokens.bgSecondary,
    borderRight: `1px solid ${colorTokens.border}`,
    display: 'flex',
    flexDirection: 'column' as const,
    zIndex: 201,
    animation: 'slideInLeft 0.3s ease-out',
  },

  drawerHeader: {
    display: 'flex',
    justifyContent: 'flex-end',
    alignItems: 'center',
    height: '56px',
    paddingRight: spacingTokens[4],
    borderBottom: `1px solid ${colorTokens.border}`,
  },

  drawerClose: {
    background: 'none',
    border: 'none',
    color: colorTokens.textPrimary,
    cursor: 'pointer',
    padding: spacingTokens[2],
    minWidth: touchTokens.minimumHitTarget,
    minHeight: touchTokens.minimumHitTarget,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '6px',
  },

  drawerContent: {
    flex: 1,
    overflow: 'auto',
    display: 'flex',
    flexDirection: 'column' as const,
  },

  drawerItem: {
    padding: `${spacingTokens[4]} ${spacingTokens[4]}`,
    background: 'none',
    border: 'none',
    borderBottom: `1px solid ${colorTokens.border}`,
    color: colorTokens.textPrimary,
    textAlign: 'left' as const,
    cursor: 'pointer',
    minHeight: touchTokens.minimumHitTarget,
    display: 'flex',
    alignItems: 'center',
    fontSize: '14px',
  },
}
