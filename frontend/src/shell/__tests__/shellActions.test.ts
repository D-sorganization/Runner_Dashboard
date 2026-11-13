/** buildShellActions tests. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { buildShellActions } from "../shellActions"

const signedOut = { loading: false, loggedIn: false } as const
const loading = { loading: true, loggedIn: false } as const
const github = { loading: false, loggedIn: true, authMethod: "github" as const }
const tailscale = { loading: false, loggedIn: true, authMethod: "tailscale" as const }
const loopback = { loading: false, loggedIn: true, authMethod: "loopback" as const }

describe("buildShellActions", () => {
  it("emits refresh and auth actions with tooltips; Classic layout is retired (#1345)", () => {
    const actions = buildShellActions(signedOut)
    const ids = actions.map((a) => a.id)
    expect(ids).toEqual(["refresh", "auth"])
    for (const a of actions) {
      expect(a.tooltip.trim().length).toBeGreaterThan(0)
      expect(a.label.length).toBeGreaterThan(0)
    }
  })

  it("auth action follows session (#3232)", () => {
    expect(buildShellActions(loading).map((a) => a.id)).toEqual(["refresh"])
    expect(buildShellActions(signedOut).find((a) => a.id === "auth")?.label).toBe("Login")
    expect(buildShellActions(github).find((a) => a.id === "auth")?.label).toBe("Logout")
    expect(buildShellActions(tailscale).map((a) => a.id)).toEqual(["refresh"])
    expect(buildShellActions(loopback).map((a) => a.id)).toEqual(["refresh"])
  })

  it("logout posts to the logout endpoint and re-probes the session", async () => {
    const onLoggedOut = vi.fn()
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }))
    const auth = buildShellActions(github, onLoggedOut).find((a) => a.id === "auth")!
    auth.onClick()
    await vi.waitFor(() => expect(onLoggedOut).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/logout",
      expect.objectContaining({ method: "POST" }),
    )
  })

  beforeEach(() => {
    window.localStorage.clear()
  })

  it.each([403, 503, "network"])("reports failed logout (%s) without claiming success", async (failure) => {
    const onLoggedOut = vi.fn()
    const onLogoutFailed = vi.fn()
    const fetchMock = vi.spyOn(globalThis, "fetch")
    if (failure === "network") fetchMock.mockRejectedValue(new TypeError("Network unavailable"))
    else fetchMock.mockResolvedValue(new Response(null, { status: failure as number }))
    const auth = buildShellActions(github, onLoggedOut, onLogoutFailed).find((a) => a.id === "auth")!
    auth.onClick()
    await vi.waitFor(() => expect(onLogoutFailed).toHaveBeenCalledTimes(1))
    expect(onLoggedOut).not.toHaveBeenCalled()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })
})
