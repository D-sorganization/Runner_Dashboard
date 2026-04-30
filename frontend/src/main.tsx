import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './legacy/App'
import { PushSettings } from './pages/PushSettings'
import { Toaster } from './primitives/Toaster'
import { BreakpointProvider } from './hooks/useBreakpoint'
import { t } from './i18n'
import './index.css'

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

function toastApi() {
  return (window as unknown as { __toaster?: { showToast: (message: string, options?: { title?: string; variant?: string; durationMs?: number }) => number } }).__toaster
}

function registerServiceWorker(): void {
  if (!('serviceWorker' in navigator)) return

  let hasReloadedForControllerChange = false

  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hasReloadedForControllerChange) return
    hasReloadedForControllerChange = true
    toastApi()?.showToast(t('sw.updateReady.message', navigator.language), {
      title: t('sw.updateReady.title', navigator.language),
      variant: 'success',
      durationMs: 12000,
    })
  })

  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .then((registration) => {
        console.log('[SW] Registered:', registration.scope)

        if (registration.waiting && navigator.serviceWorker.controller) {
          toastApi()?.showToast(t('sw.updateReady.message', navigator.language), {
            title: t('sw.updateReady.title', navigator.language),
            variant: 'success',
            durationMs: 12000,
          })
        }

        registration.addEventListener('updatefound', () => {
          const installingWorker = registration.installing
          if (!installingWorker) return
          if (navigator.serviceWorker.controller) {
            toastApi()?.showToast(t('sw.updateFound.message', navigator.language), {
              variant: 'info',
              durationMs: 4000,
            })
          }
          installingWorker.addEventListener('statechange', () => {
            if (installingWorker.state === 'installed' && navigator.serviceWorker.controller) {
              toastApi()?.showToast(t('sw.updateReady.message', navigator.language), {
                title: t('sw.updateReady.title', navigator.language),
                variant: 'success',
                durationMs: 12000,
              })
            }
          })
        })
      })
      .catch((err) => {
        console.warn('[SW] Registration failed:', err)
      })
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BreakpointProvider>
      <Toaster>
        {isPushSettingsRoute(window.location.pathname) ? (
          <PushSettings />
        ) : (
          <App initialTab={initialTabFromPathname(window.location.pathname)} />
        )}
      </Toaster>
    </BreakpointProvider>
  </React.StrictMode>,
)

registerServiceWorker()
