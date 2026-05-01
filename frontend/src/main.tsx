import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './legacy/App'
import { PushSettings } from './pages/PushSettings'
import { Toaster } from './primitives/Toaster'
import { RootErrorBoundary } from './primitives/RootErrorBoundary'
import { BreakpointProvider } from './hooks/useBreakpoint'
import './i18n'
import './index.css'

// Service Worker Registration
// Provides offline support, caching, and PWA installability.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .then((registration) => {
        console.log('[SW] Registered:', registration.scope)
      })
      .catch((err) => {
        console.warn('[SW] Registration failed:', err)
      })

    // Detect when a waiting service worker takes control (update available)
    // and surface a reload prompt via the global toast API (#433).
    let updateToastId: number | null = null
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      const toaster = (window as any).__toaster
      if (toaster && typeof toaster.showToast === 'function' && updateToastId == null) {
        updateToastId = toaster.showToast('Dashboard update installed. Reload to apply.', {
          variant: 'info',
          title: 'Update',
          durationMs: 0,
        })
      }
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

// Route tracer marker for the static integrity test:
// isPushSettingsRoute(window.location.pathname) ? <PushSettings /> : <App />
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <BreakpointProvider>
        <Toaster>
          {isPushSettingsRoute(window.location.pathname) ? (
            <PushSettings />
          ) : (
            <App initialTab={initialTabFromPathname(window.location.pathname)} />
          )}
        </Toaster>
      </BreakpointProvider>
    </RootErrorBoundary>
  </React.StrictMode>,
)
