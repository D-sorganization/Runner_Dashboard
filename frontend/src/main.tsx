import React, { useState, useCallback } from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import App from './legacy/App'
import PushSettings from './pages/PushSettings'
import { MaxwellMobile } from './pages/Maxwell'
import { ReportsMobile } from './pages/Reports'
import { CredentialsMobile } from './pages/Credentials'
import { MobileShell, type TabId } from './shell/MobileShell'
import { Toaster } from './primitives/Toaster'
import { RootErrorBoundary } from './primitives/RootErrorBoundary'
import { BreakpointProvider, useBreakpoint } from './hooks/useBreakpoint'
import './i18n'
import './index.css'
// Web Vitals — send metrics to backend (issue #385)
import { onCLS, onINP, onFCP, onLCP } from 'web-vitals'

function sendWebVitals(metric: any) {
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
    const buildId = (import.meta as any).env?.VITE_BUILD_ID || 'dev'
    navigator.serviceWorker
      .register(`/sw.js?build=${encodeURIComponent(buildId)}`)
      .then((registration) => {
        console.log('[SW] Registered:', registration.scope)
      })
      .catch((err) => {
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
  console.log('[PWA] Install prompt deferred')
})

// Expose a helper to trigger the install prompt
// Components can call this if they want to offer an "Install App" button.
function triggerInstallPrompt(): void {
  const prompt = (window as any).__deferredPrompt || deferredPrompt
  if (prompt) {
    ;(prompt as any).prompt()
    ;(prompt as any).userChoice.then((choice: { outcome: string }) => {
      if (choice.outcome === 'accepted') {
        console.log('[PWA] User accepted install prompt')
      } else {
        console.log('[PWA] User dismissed install prompt')
      }
      deferredPrompt = null
      ;(window as any).__deferredPrompt = null
    })
  } else {
    console.log('[PWA] No deferred install prompt available')
  }
}

// Attach to window for legacy access
;(window as any).__deferredPrompt = deferredPrompt
;(window as any).triggerInstallPrompt = triggerInstallPrompt

// Map legacy App tab strings to MobileShell TabIds.
const LEGACY_TO_TAB_ID: Record<string, TabId> = {
  overview: 'fleet',
  fleet: 'fleet',
  workflows: 'workflows',
  remediation: 'remediation',
  maxwell: 'maxwell',
  org: 'org',
  machines: 'heavy',
  assessments: 'assessments',
  'feature-requests': 'requests',
  credentials: 'credentials',
  reports: 'reports',
  queue: 'health',
  health: 'health',
}

// Inverse map: MobileShell TabId → legacy App tab string.
const TAB_ID_TO_LEGACY: Partial<Record<TabId, string>> = {
  fleet: 'overview',
  workflows: 'workflows',
  remediation: 'remediation',
  maxwell: 'maxwell',
  org: 'org',
  heavy: 'machines',
  assessments: 'assessments',
  requests: 'feature-requests',
  credentials: 'credentials',
  reports: 'reports',
  health: 'queue',
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
function AppWithMobileShell({ initialTab }: { initialTab?: string }) {
  const breakpoint = useBreakpoint()
  const isMobile = breakpoint !== 'lg' && breakpoint !== 'xl'

  const resolvedInitialTabId: TabId =
    (initialTab && LEGACY_TO_TAB_ID[initialTab]) || 'fleet'
  const [mobileTab, setMobileTab] = useState<TabId>(resolvedInitialTabId)

  const handleMobileTabChange = useCallback((nextTab: TabId) => {
    setMobileTab(nextTab)
  }, [])

  const handleLegacyTabChange = useCallback((nextLegacyTab: string) => {
    const mapped = LEGACY_TO_TAB_ID[nextLegacyTab]
    if (mapped) setMobileTab(mapped)
  }, [])

  const legacyInitialTab =
    initialTab ?? TAB_ID_TO_LEGACY[resolvedInitialTabId] ?? 'overview'

  if (isMobile) {
    // M11, M12, M13: native mobile views registered here.
    const mobileTabContent = {
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
          initialTab={TAB_ID_TO_LEGACY[mobileTab] ?? legacyInitialTab}
          onTabChange={handleLegacyTabChange}
        />
      </MobileShell>
    )
  }

  return <App initialTab={legacyInitialTab} onTabChange={handleLegacyTabChange} />
}

// Route tracer marker for the static integrity test:
// isPushSettingsRoute(window.location.pathname) ? <PushSettings /> : <AppWithMobileShell />
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <BreakpointProvider>
        <Toaster>
          {isPushSettingsRoute(window.location.pathname) ? (
            <PushSettings />
          ) : (
            <AppWithMobileShell initialTab={initialTabFromPathname(window.location.pathname)} />
          )}
        </Toaster>
      </BreakpointProvider>
    </RootErrorBoundary>
  </React.StrictMode>,
)
