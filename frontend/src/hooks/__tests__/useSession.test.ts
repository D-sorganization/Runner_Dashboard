/** useSession tests — signed-out, GitHub, Tailscale, loopback (#3232). */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useSession, type DashboardPrincipal } from "../useSession";

const github: DashboardPrincipal = {
  id: "owner",
  type: "human",
  name: "Fleet Owner",
  github_username: "fleet-owner",
};
const tailscale: DashboardPrincipal = {
  id: "tailscale:alice",
  type: "human",
  name: "Alice",
};
const loopback: DashboardPrincipal = {
  id: "__loopback__",
  type: "human",
  name: "Loopback development admin",
};

function mockMe(principal: DashboardPrincipal | null) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (!url.includes("/api/auth/me")) return new Response(null, { status: 404 });
    return principal
      ? Response.json(principal)
      : new Response(null, { status: 401 });
  });
}

describe("useSession", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("reports signed-out when /api/auth/me returns 401", async () => {
    mockMe(null);
    const { result } = renderHook(() => useSession({ pollMs: 0 }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.loggedIn).toBe(false);
  });

  it.each([
    [github, "github", "fleet-owner"],
    [tailscale, "tailscale", "alice"],
    [loopback, "loopback", "Loopback development admin"],
  ] as const)("reports %s auth from /api/auth/me", async (principal, method, displayName) => {
    mockMe(principal);
    const { result } = renderHook(() => useSession({ pollMs: 0 }));
    await waitFor(() => expect(result.current.loggedIn).toBe(true));
    expect(result.current.authMethod).toBe(method);
    expect(result.current.displayName).toBe(displayName);
    if (method === "loopback") {
      expect(result.current.loopbackHint).toMatch(/Tailscale URL/i);
    }
  });

  it("re-probes on refresh after logout", async () => {
    let principal: DashboardPrincipal | null = github;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (!String(input).includes("/api/auth/me")) return new Response(null, { status: 404 });
      return principal ? Response.json(principal) : new Response(null, { status: 401 });
    });
    const { result } = renderHook(() => useSession({ pollMs: 0 }));
    await waitFor(() => expect(result.current.loggedIn).toBe(true));
    principal = null;
    await act(async () => result.current.refresh());
    await waitFor(() => expect(result.current.loggedIn).toBe(false));
  });
});
