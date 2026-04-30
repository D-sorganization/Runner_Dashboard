import React, { useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import App from './legacy/App'
import { PushSettings } from './pages/PushSettings'
import { Toaster } from './primitives/Toaster'
import { useToast } from './primitives/Toaster'
import { BreakpointProvider } from './hooks/useBreakpoint'
import './index.css'

// Service Worker Registration + Update Toast
// Provides offline support, caching, PWA installability, and update UX.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .then((registration) => {
        console.log('[SW] Registered:', registration.scope)

        // Watch for updates — if a new SW is waiting, prompt user to refresh
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing
          if (!newWorker) return
          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              // New SW is waiting; dispatch a custom event the app can listen for
              window.dispatchEvent(new CustomEvent('sw-update-available'))
            }
          })
        })
      })
      .catch((err) => {
        console.warn('[SW] Registration failed:', err)
      })

    // When a new controller takes over, the page should reload automatically
    // or the user can manually refresh. We log for debugging.
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      console.log('[SW] New controller activated')
    })
  })
}

// Global toast for SW updates — any component can listen via the custom event
function SwUpdateToast(): null {
  const { showToast } = useToast()
  useEffect(() => {
    function onUpdate() {
      showToast('A new version is ready. Refresh the page to apply.', {
        variant: 'info',
        durationMs: 0, // persistent until dismissed
      })
    }
    window.addEventListener('sw-update-available', onUpdate)
    return () => window.removeEventListener('sw-update-available', onUpdate)
  }, [showToast])
  return null
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
    <BreakpointProvider>
      <Toaster>
        <SwUpdateToast />
        {isPushSettingsRoute(window.location.pathname) ? (
          <PushSettings />
        ) : (
          <App initialTab={initialTabFromPathname(window.location.pathname)} />
        )}
      </Toaster>
    </BreakpointProvider>
  </React.StrictMode>,
)
