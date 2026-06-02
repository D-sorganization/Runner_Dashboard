/* eslint-disable react-refresh/only-export-components -- main.tsx is the app entry point, not a component module */
import React, { useState, useCallback } from 'react'
import ReactDOM from 'react-dom/client'
import App from './legacy/App'
import PushSettings from './pages/PushSettings'
import { QueueMobile } from './pages/Queue'
import { MaxwellMobile } from './pages/Maxwell'
import { ReportsMobile } from './pages/Reports'
import { CredentialsMobile } from './pages/Credentials'
import { FleetMobile } from './pages/Fleet'
import { MobileShell, type TabId } from './shell/MobileShell'
import { DesktopShell, type ShellAction } from './shell/DesktopShell'
import { ActiveProviderControl } from './shell/ActiveProviderControl'
import { resolveDesktopShellLayout, LAYOUT_STORAGE_KEY } from './shell/layoutFlag'
import { useProviderRegistry } from './lib/useProviderRegistry'
import { Toaster } from './primitives/Toaster'
import { RootErrorBoundary } from './primitives/RootErrorBoundary'
import { BreakpointProvider, useBreakpoint } from './hooks/useBreakpoint'
import { ThemeProvider } from './design/ThemeProvider'
import { useThemeContext } from './design/ThemeContext'
import { ThemeSelector } from './components/ThemeSelector'
import { DensityToggle } from './components/DensityToggle'
import { SkeletonCard } from './primitives/Skeleton'
import './index.css'
// Web Vitals — send metrics to backend (issue #385)
import { onCLS, onINP, onFCP, onLCP } from 'web-vitals'

function sendWebVitals(metric: { name: string; value: number; rating?: string; delta?: number; id?: string; navigationType?: string }) {
  const payload = {
    route: window.location.pathname,
    metrics: [{
      name: metric.name,
      value: metric.value,
      rating: metric.rating || '',
      delta: metric.delta || null,
      id: metric.id || '',
      navigation_type: metric.navigationType || '',
    }],
  }
  fetch('/api/metrics/web-vitals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {})
}

onCLS(sendWebVitals)
onINP(sendWebVitals)
onFCP(sendWebVitals)
onLCP(sendWebVitals)

// Service Worker Registration
// Provides offline support, caching, and PWA installability.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const toaster = (window as any).__toaster
    if (toaster && typeof toaster.showToast === 'function') {
      toaster.showToast('A dashboard update is ready.', {
        title: 'New version',
        durationMs: 0,
        actionLabel: 'Reload',
        onAction: () => window.location.reload(),
      })
    } else {
      window.location.reload()
    }
  })

  window.addEventListener('load', () => {
    const buildId = (import.meta.env as Record<string, string>)?.VITE_BUILD_ID || 'dev'
    navigator.serviceWorker
      .register(`/sw.js?build=${encodeURIComponent(buildId)}`)
      .then((registration) => {
        // eslint-disable-next-line no-console
        console.log('[SW] Registered:', registration.scope)
      })
      .catch((err) => {
        // eslint-disable-next-line no-console
        console.warn('[SW] Registration failed:', err)
      })
  })
}

// PWA Install Prompt Handling
// Captures the beforeinstallprompt event so the app can suggest installation.
let deferredPrompt: Event | null = null

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault()
  deferredPrompt = e
  // eslint-disable-next-line no-console
  console.log('[PWA] Install prompt deferred')
})

// Expose a helper to trigger the install prompt
// Components can call this if they want to offer an "Install App" button.
function triggerInstallPrompt(): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any
  const prompt = w.__deferredPrompt || deferredPrompt
  if (prompt) {
    prompt.prompt()
    prompt.userChoice.then((choice: { outcome: string }) => {
      if (choice.outcome === 'accepted') {
        // eslint-disable-next-line no-console
        console.log('[PWA] User accepted install prompt')
      } else {
        // eslint-disable-next-line no-console
        console.log('[PWA] User dismissed install prompt')
      }
      deferredPrompt = null
      w.__deferredPrompt = null
    })
  } else {
    // eslint-disable-next-line no-console
    console.log('[PWA] No deferred install prompt available')
  }
}

// Attach to window for legacy access
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _win = window as any
_win.__deferredPrompt = deferredPrompt
_win.triggerInstallPrompt = triggerInstallPrompt

// The MobileShell is now driven by the single nav registry (issue #821), so a
// mobile TabId *is* the legacy App tab string — no translation table is needed.
// A couple of legacy aliases are normalized to their canonical registry tabId.
const TAB_ID_ALIASES: Record<string, string> = {
  fleet: 'overview',
  health: 'queue',
}

function normalizeTabId(tab: string): TabId {
  return TAB_ID_ALIASES[tab] ?? tab
}

function isPushSettingsRoute(pathname: string): boolean {
  const normalized = pathname.replace(/\/+$/, '') || '/'
  return normalized === '/settings/push'
}

const PATHNAME_TO_TAB: Record<string, string> = {
  '/dispatch': 'agent-dispatch',
  '/queue': 'queue',
  '/maxwell': 'maxwell',
  '/remediate': 'remediation',
}

function initialTabFromPathname(pathname: string): string | undefined {
  const normalized = pathname.replace(/\/+$/, '') || '/'
  return PATHNAME_TO_TAB[normalized]
}

/**
 * AppWithMobileShell wraps the legacy App in a MobileShell on small viewports.
 * Native mobile components (M12, M13, ...) are passed via tabContent so they
 * supersede the legacy App for their respective drawer tabs.
 */
/**
 * Build the modern desktop shell's action bar. These actions are deliberately
 * self-contained (no reach into the legacy App internals) so the shell stays
 * orthogonal and reversible: Refresh reloads dashboard data, Login/Logout
 * toggles the GitHub session, and "Classic layout" pins the legacy shell via
 * localStorage and reloads — the visible escape hatch back to the old UI.
 */
function buildShellActions(): ShellAction[] {
  const isLoggedIn =
    typeof document !== 'undefined' && document.cookie.includes('dashboard_session')
  return [
    {
      id: 'refresh',
      label: 'Refresh',
      tooltip: 'Reload the dashboard to fetch the latest fleet, queue and run data.',
      onClick: () => window.location.reload(),
    },
    {
      id: 'auth',
      label: isLoggedIn ? 'Logout' : 'Login',
      tooltip: isLoggedIn
        ? 'Sign out of the dashboard GitHub session.'
        : 'Sign in with GitHub to enable runner and workflow controls.',
      onClick: () => {
        if (isLoggedIn) {
          fetch('/api/auth/logout', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
          }).then(() => window.location.reload())
        } else {
          window.location.href = '/api/auth/github'
        }
      },
    },
    {
      id: 'classic-layout',
      label: 'Classic layout',
      tooltip: 'Switch back to the legacy top-toolstrip layout (reversible; stored per browser).',
      onClick: () => {
        try {
          window.localStorage.setItem(LAYOUT_STORAGE_KEY, 'legacy')
        } catch {
          /* storage unavailable — non-fatal */
        }
        window.location.reload()
      },
    },
  ]
}

/**
 * Persistent/global provider control for the shell topbar (#811). Fetches the
 * unified registry once and renders the always-visible ActiveProviderControl;
 * renders nothing until the registry is available so the topbar never flashes a
 * broken control. Clicking "Fix login" jumps to the Credentials tab.
 */
function ShellActiveProvider() {
  const { registry } = useProviderRegistry()
  if (!registry) return null
  return (
    <ActiveProviderControl
      registry={registry}
      onRequestLogin={() => {
        try {
          window.location.assign('/?tab=credentials')
        } catch {
          /* navigation unavailable — non-fatal */
        }
      }}
    />
  )
}

/**
 * Persistent theme picker for the desktop shell header (#820). Reads the shared
 * theme context (single source of truth via ThemeProvider/useTheme) so all 13
 * fleet themes are reachable from the always-visible topbar.
 */
function ShellThemeSelector() {
  const { mode, setMode } = useThemeContext()
  return <ThemeSelector currentMode={mode} onThemeChange={setMode} />
}

function AppWithMobileShell({ initialTab }: { initialTab?: string }) {
  const breakpoint = useBreakpoint()
  const isMobile = breakpoint !== 'lg' && breakpoint !== 'xl'

  const resolvedInitialTabId: TabId = normalizeTabId(initialTab ?? 'overview')
  const [mobileTab, setMobileTab] = useState<TabId>(resolvedInitialTabId)

  // Desktop modern-shell navigation is keyed on the legacy App tab string so the
  // sidebar / slim toolstrip and the mounted page body stay in lockstep.
  const [desktopTab, setDesktopTab] = useState<string>(initialTab ?? 'overview')

  const handleMobileTabChange = useCallback((nextTab: TabId) => {
    setMobileTab(nextTab)
  }, [])

  const handleLegacyTabChange = useCallback((nextLegacyTab: string) => {
    setMobileTab(normalizeTabId(nextLegacyTab))
    setDesktopTab(nextLegacyTab)
  }, [])

  const legacyInitialTab = initialTab ?? resolvedInitialTabId

  if (isMobile) {
    // M09-M13: native mobile views registered here, keyed by registry tabId.
    const mobileTabContent = {
      overview: <FleetMobile />,
      queue: <QueueMobile />,
      maxwell: <MaxwellMobile />,
      reports: <ReportsMobile />,
      credentials: <CredentialsMobile />,
    } as Partial<Record<TabId, React.ReactNode>>

    return (
      <MobileShell
        currentTab={mobileTab}
        onTabChange={handleMobileTabChange}
        tabContent={mobileTabContent as Record<TabId, React.ReactNode>}
      >
        <App
          initialTab={mobileTab}
          onTabChange={handleLegacyTabChange}
        />
      </MobileShell>
    )
  }

  // Desktop. The modern shell (#802) is the default but fully reversible: when
  // the layout flag resolves to legacy (localStorage `dashboard.layout=legacy`
  // or VITE_DESKTOP_SHELL opt-out) we render the untouched legacy App with its
  // own top toolstrip. Otherwise the new DesktopShell (sidebar + slim toolstrip
  // + tooltips) owns navigation and mounts the legacy App chromeless + tab-
  // controlled, so every existing page renders unchanged inside <main>.
  const env = (import.meta.env as Record<string, string | undefined>)?.VITE_DESKTOP_SHELL
  const useModernShell = resolveDesktopShellLayout({ env })

  if (!useModernShell) {
    return <App initialTab={legacyInitialTab} onTabChange={handleLegacyTabChange} />
  }

  return (
    <DesktopShell
      activeTabId={desktopTab}
      onSelect={setDesktopTab}
      actions={buildShellActions()}
      headerExtra={
        <>
          <DensityToggle />
          <ShellThemeSelector />
          <ShellActiveProvider />
        </>
      }
    >
      <App
        initialTab={legacyInitialTab}
        activeTab={desktopTab}
        chromeless
        onTabChange={handleLegacyTabChange}
      />
    </DesktopShell>
  )
}

// Route tracer marker for the static integrity test:
// isPushSettingsRoute(window.location.pathname) ? <PushSettings /> : <AppWithMobileShell />
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <React.Suspense
      fallback={
        <div role="status" aria-label="Loading dashboard" className="app-loading" style={{ padding: 24, maxWidth: 1440, margin: '0 auto' }}>
          <SkeletonCard lines={4} />
        </div>
      }
    >
      <RootErrorBoundary>
        <ThemeProvider>
          <BreakpointProvider>
            <Toaster>
              {isPushSettingsRoute(window.location.pathname) ? (
                <PushSettings />
              ) : (
                <AppWithMobileShell initialTab={initialTabFromPathname(window.location.pathname)} />
              )}
            </Toaster>
          </BreakpointProvider>
        </ThemeProvider>
      </RootErrorBoundary>
    </React.Suspense>
  </React.StrictMode>,
)
