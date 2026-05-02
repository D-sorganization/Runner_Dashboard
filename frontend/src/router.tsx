/**
 * router.tsx – React Router v6 route configuration (issue #375).
 *
 * Each major page is loaded via React.lazy() so Vite produces a separate
 * chunk per route.  The legacy App (which contains Fleet, Maxwell, Queue,
 * Remediation, and all other tabs) is the largest chunk; future extractions
 * from legacy/App.tsx (tracked in #403) will progressively shrink it.
 *
 * Route map
 * ---------
 *  /                   -> Fleet (overview tab, default landing page)
 *  /queue              -> Queue tab
 *  /maxwell            -> Maxwell tab
 *  /dispatch           -> Agent Dispatch page
 *  /settings/push      -> Push Settings page
 *  *                   -> Fallback to Fleet (preserves existing behaviour)
 *
 * To activate: replace <AppWithMobileShell /> in main.tsx with <AppRouter />.
 */

import React, { Suspense } from "react"
import {
  createBrowserRouter,
  RouterProvider,
  Navigate,
} from "react-router-dom"
import { RootErrorBoundary } from "./primitives/RootErrorBoundary"

// ---- Lazy page chunks -------------------------------------------------------
// Each import() becomes a separate Vite chunk (route-level code splitting).

const LazyQueue = React.lazy(() =>
  import("./pages/Queue").then((m) => ({ default: m.QueueTab ?? (m as any).default })),
)

const LazyAgentDispatch = React.lazy(() =>
  import("./pages/AgentDispatch").then((m) => ({
    default: m.AgentDispatchPage ?? (m as any).default,
  })),
)

const LazyPushSettings = React.lazy(() =>
  import("./pages/PushSettings").then((m) => ({ default: m.default ?? (m as any).PushSettings })),
)

// The legacy App hosts Fleet, Maxwell, Remediation, Org, Heavy, and more.
// It is lazy-loaded as its own chunk; individual tabs extracted in #403
// will become separate lazy imports over time.
const LazyLegacyApp = React.lazy(() => import("./legacy/App"))

// ---- Suspense fallback ------------------------------------------------------

function PageSpinner() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100dvh",
        fontSize: 14,
        color: "var(--color-fg-muted, #888)",
      }}
    >
      Loading...
    </div>
  )
}

function withSuspense(element: React.ReactElement) {
  return <Suspense fallback={<PageSpinner />}>{element}</Suspense>
}

// ---- Route definitions ------------------------------------------------------

export const router = createBrowserRouter([
  {
    errorElement: <RootErrorBoundary />,
    children: [
      {
        path: "/",
        element: withSuspense(<LazyLegacyApp initialTab="overview" />),
      },
      {
        path: "/queue",
        element: withSuspense(<LazyQueue />),
      },
      {
        path: "/dispatch",
        element: withSuspense(<LazyAgentDispatch />),
      },
      {
        path: "/settings/push",
        element: withSuspense(<LazyPushSettings />),
      },
      {
        path: "/maxwell",
        element: withSuspense(<LazyLegacyApp initialTab="maxwell" />),
      },
      {
        path: "/remediate",
        element: withSuspense(<LazyLegacyApp initialTab="remediation" />),
      },
      // Catch-all: redirect unknown routes back to Fleet.
      {
        path: "*",
        element: <Navigate to="/" replace />,
      },
    ],
  },
])

// ---- RouterProvider wrapper --------------------------------------------------

/**
 * AppRouter wraps the entire app in a BrowserRouter.
 * Mount this in main.tsx in place of the bare <AppWithMobileShell />.
 *
 * The QueryClientProvider, RootErrorBoundary, BreakpointProvider, and Toaster
 * wrappers in main.tsx remain unchanged outside the router.
 */
export function AppRouter() {
  return <RouterProvider router={router} />
}
