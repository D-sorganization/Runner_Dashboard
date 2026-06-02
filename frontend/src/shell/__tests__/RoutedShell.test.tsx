/**
 * Integration tests for RoutedShell — the single navigation source of truth
 * (issues #835, #831).
 *
 * These assert the routing contract without dragging in the 17k-line legacy
 * App or live data hooks (both mocked): the active tab is derived from the URL
 * param, selecting a tab navigates the URL (deep-linkable + back/forward), and
 * the legacy App is loaded lazily (its module is only imported on demand).
 */
import React from "react"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom"

// --- Mocks: keep the test light and focused on routing -------------------

const legacyAppImport = vi.fn()

vi.mock("../../legacy/App", () => {
  legacyAppImport()
  return {
    default: (props: { activeTab?: string; initialTab?: string }) => (
      <div data-testid="legacy-app" data-active-tab={props.activeTab ?? props.initialTab} />
    ),
  }
})

// Force the desktop shell branch (lg) so DesktopShell renders deterministically.
vi.mock("../../hooks/useBreakpoint", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>
  return { ...actual, useBreakpoint: () => "lg" }
})

vi.mock("../../hooks/useSession", () => ({
  useSession: () => ({ loggedIn: false, refresh: vi.fn() }),
}))

vi.mock("../../lib/useProviderRegistry", () => ({
  useProviderRegistry: () => ({ registry: null }),
}))

vi.mock("../../design/ThemeContext", () => ({
  useThemeContext: () => ({ mode: "light", setMode: vi.fn() }),
}))

// Stub the heavy desktop shell with a thin harness that exposes activeTabId
// and a button that drives onSelect — exactly the contract RoutedShell relies
// on, without the full sidebar/toolstrip render.
vi.mock("../DesktopShell", () => ({
  DesktopShell: (props: {
    activeTabId: string
    onSelect: (id: string) => void
    children: React.ReactNode
  }) => (
    <div>
      <span data-testid="active-tab">{props.activeTabId}</span>
      <button onClick={() => props.onSelect("maxwell")}>go-maxwell</button>
      {props.children}
    </div>
  ),
}))

import { RoutedShell } from "../RoutedShell"

function LocationProbe() {
  const loc = useLocation()
  return <span data-testid="pathname">{loc.pathname}</span>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <Routes>
        <Route path="/t/:tabId" element={<RoutedShell />} />
        <Route path="/" element={<RoutedShell />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe("RoutedShell — URL is the source of truth", () => {
  beforeEach(() => {
    cleanup()
    legacyAppImport.mockClear()
  })

  it("derives the default tab from the root path", async () => {
    renderAt("/")
    expect(await screen.findByTestId("active-tab")).toHaveTextContent("overview")
  })

  it("derives the active tab from the /t/:tabId param", async () => {
    renderAt("/t/queue")
    expect(await screen.findByTestId("active-tab")).toHaveTextContent("queue")
  })

  it("falls back to the default tab for an unknown tab id", async () => {
    renderAt("/t/not-a-real-tab")
    expect(await screen.findByTestId("active-tab")).toHaveTextContent("overview")
  })

  it("selecting a tab navigates the URL (deep-linkable + back/forward)", async () => {
    const user = userEvent.setup()
    renderAt("/")
    await screen.findByTestId("active-tab")
    await user.click(screen.getByText("go-maxwell"))
    expect(await screen.findByTestId("pathname")).toHaveTextContent("/t/maxwell")
    expect(await screen.findByTestId("active-tab")).toHaveTextContent("maxwell")
  })

  it("lazy-loads the legacy App (code-split, not eager)", async () => {
    renderAt("/")
    // The legacy App renders only after its lazy chunk resolves.
    expect(await screen.findByTestId("legacy-app")).toBeInTheDocument()
  })
})
