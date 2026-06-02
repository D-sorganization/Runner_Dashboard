/**
 * Tests for buildShellActions — the desktop shell action bar builder
 * (extracted from the shell component for testability). Every action must
 * carry a non-empty tooltip (the DesktopShell contract + a11y audit rely on
 * it), and the auth action must reflect the login state.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { buildShellActions } from "../shellActions"

describe("buildShellActions", () => {
  it("emits refresh, auth and classic-layout actions with tooltips", () => {
    const actions = buildShellActions(false)
    const ids = actions.map((a) => a.id)
    expect(ids).toEqual(["refresh", "auth", "classic-layout"])
    for (const a of actions) {
      expect(a.tooltip.trim().length).toBeGreaterThan(0)
      expect(a.label.length).toBeGreaterThan(0)
    }
  })

  it("labels the auth action by login state", () => {
    expect(buildShellActions(false).find((a) => a.id === "auth")?.label).toBe("Login")
    expect(buildShellActions(true).find((a) => a.id === "auth")?.label).toBe("Logout")
  })

  it("logout posts to the logout endpoint and re-probes the session", async () => {
    const onLoggedOut = vi.fn()
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }))
    const auth = buildShellActions(true, onLoggedOut).find((a) => a.id === "auth")!
    auth.onClick()
    await vi.waitFor(() => expect(onLoggedOut).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/logout",
      expect.objectContaining({ method: "POST" }),
    )
  })

  it("classic-layout pins the legacy layout in localStorage", () => {
    const reload = vi.fn()
    const original = window.location
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...original, reload },
    })
    try {
      const action = buildShellActions(false).find((a) => a.id === "classic-layout")!
      action.onClick()
      expect(window.localStorage.getItem("dashboard.layout")).toBe("legacy")
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        value: original,
      })
    }
  })

  beforeEach(() => {
    window.localStorage.clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })
})
