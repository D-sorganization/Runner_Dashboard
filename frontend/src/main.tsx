import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import PushSettings from './pages/PushSettings'
import { RoutedShell } from './shell/RoutedShell'
import { Toaster } from './primitives/Toaster'
import { RootErrorBoundary } from './primitives/RootErrorBoundary'
import { BreakpointProvider } from './hooks/useBreakpoint'
import { ThemeProvider } from './design/ThemeProvider'
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

/**
 * AppRoutes — the single navigation source of truth (issue #835).
 *
 * React Router owns the URL, so every navRegistry tab is a real, deep-linkable
 * route: "/" lands on Fleet, "/t/:tabId" opens any tab, and "/settings/push"
 * keeps its dedicated deep link. Selecting a tab pushes a URL (see
 * RoutedShell), so bookmarks, sharing and browser back/forward all work. This
 * replaces the previous hand-rolled `window.location.pathname` navigation; the
 * old unmounted `router.tsx` has been retired in favour of this single source.
 *
 * The legacy App is loaded lazily inside RoutedShell, so the ~485KB monolith
 * code-splits into its own chunk (issue #831).
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/settings/push" element={<PushSettings />} />
      <Route path="/t/:tabId" element={<RoutedShell />} />
      <Route path="/" element={<RoutedShell />} />
      {/* Unknown routes fall back to Fleet, preserving prior behaviour. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

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
              <BrowserRouter>
                <AppRoutes />
              </BrowserRouter>
            </Toaster>
          </BreakpointProvider>
        </ThemeProvider>
      </RootErrorBoundary>
    </React.Suspense>
  </React.StrictMode>,
)
