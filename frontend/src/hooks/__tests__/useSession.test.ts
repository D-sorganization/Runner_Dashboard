/**
 * Tests for useSession — reactive dashboard-session probe (#842).
 *
 * TDD: authored alongside the hook. Verifies the cookie name-boundary match,
 * the reactive re-read on focus, and the `refresh()` escape hatch.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSession, hasSessionCookie } from "../useSession";

describe("hasSessionCookie", () => {
  it("matches the dashboard_session cookie by name boundary", () => {
    expect(hasSessionCookie("dashboard_session=abc")).toBe(true);
    expect(hasSessionCookie("foo=1; dashboard_session=abc; bar=2")).toBe(true);
  });

  it("does not false-positive on a substring match", () => {
    expect(hasSessionCookie("not_dashboard_session_x=1")).toBe(false);
    expect(hasSessionCookie("")).toBe(false);
    expect(hasSessionCookie("other=1")).toBe(false);
  });
});

describe("useSession", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reflects the initial cookie state", () => {
    const { result } = renderHook(() =>
      useSession({ readCookie: () => "dashboard_session=x", pollMs: 0 }),
    );
    expect(result.current.loggedIn).toBe(true);
  });

  it("reports logged-out when no session cookie is present", () => {
    const { result } = renderHook(() =>
      useSession({ readCookie: () => "theme=dark", pollMs: 0 }),
    );
    expect(result.current.loggedIn).toBe(false);
  });

  it("re-reads the cookie when refresh() is called (no reload needed)", () => {
    let cookie = "theme=dark";
    const { result } = renderHook(() =>
      useSession({ readCookie: () => cookie, pollMs: 0 }),
    );
    expect(result.current.loggedIn).toBe(false);

    cookie = "dashboard_session=signed-in";
    act(() => result.current.refresh());
    expect(result.current.loggedIn).toBe(true);

    cookie = "theme=dark";
    act(() => result.current.refresh());
    expect(result.current.loggedIn).toBe(false);
  });

  it("re-reads on window focus", () => {
    let cookie = "theme=dark";
    const { result } = renderHook(() =>
      useSession({ readCookie: () => cookie, pollMs: 0 }),
    );
    cookie = "dashboard_session=x";
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(result.current.loggedIn).toBe(true);
  });

  it("re-reads on the poll interval", () => {
    let cookie = "theme=dark";
    const { result } = renderHook(() =>
      useSession({ readCookie: () => cookie, pollMs: 1000 }),
    );
    cookie = "dashboard_session=x";
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.loggedIn).toBe(true);
  });
});
