/**
 * useSession — reactive dashboard-session probe (issue #842).
 *
 * Background: `buildShellActions()` in `main.tsx` previously read
 * `document.cookie` exactly once at render time, so the topbar could show
 * "Login" while authenticated (or "Logout" after signing out) until a manual
 * reload. This hook turns that one-shot read into reactive state.
 *
 * Strategy: the dashboard session is carried by the `dashboard_session`
 * cookie. The cookie is not observable via an event, so we (a) read it on
 * mount, (b) re-read on `focus` / `visibilitychange` (covers the common case:
 * the user completes the GitHub OAuth round-trip in the same tab and returns),
 * and (c) re-read on a light interval. `refresh()` is exposed so callers can
 * force a re-read immediately after a logout fetch resolves.
 *
 * LoD: callers receive a flat `{ loggedIn, refresh }` — they never parse the
 * cookie themselves. `readCookie` is injectable so tests drive the hook
 * deterministically without touching `document.cookie`.
 */
import { useCallback, useEffect, useRef, useState } from "react";

/** The cookie whose presence indicates an authenticated dashboard session. */
export const SESSION_COOKIE = "dashboard_session";

/** Default poll interval for the cookie re-read (ms). */
export const SESSION_POLL_MS = 5000;

/** Read the raw cookie string from the document (overridable in tests). */
export type CookieReader = () => string;

function defaultReadCookie(): string {
  return typeof document !== "undefined" ? document.cookie : "";
}

/**
 * Whether a `dashboard_session` cookie is present in a cookie string.
 *
 * Contract: matches the cookie by name boundary so a cookie merely *containing*
 * the substring (e.g. `not_dashboard_session_x`) does not produce a false
 * positive — the old substring check (`document.cookie.includes(...)`) was
 * loose. Postcondition: returns a boolean.
 */
export function hasSessionCookie(cookie: string): boolean {
  if (!cookie) return false;
  return cookie
    .split(";")
    .map((part) => part.trim())
    .some((part) => {
      const eq = part.indexOf("=");
      const name = eq === -1 ? part : part.slice(0, eq);
      return name === SESSION_COOKIE;
    });
}

export interface SessionState {
  /** True when a dashboard session cookie is currently present. */
  loggedIn: boolean;
  /** Force an immediate re-read of the cookie (e.g. after logout resolves). */
  refresh: () => void;
}

export interface UseSessionOptions {
  /** Injectable cookie reader (tests). Defaults to `document.cookie`. */
  readCookie?: CookieReader;
  /** Poll interval in ms. Pass 0 to disable interval polling (tests). */
  pollMs?: number;
}

/**
 * Reactive dashboard-session hook. Re-evaluates the session cookie on mount,
 * on window focus / tab visibility changes, and on a light interval, so the
 * topbar Login/Logout label stays correct without a full page reload.
 */
export function useSession(options: UseSessionOptions = {}): SessionState {
  const readCookie = options.readCookie ?? defaultReadCookie;
  const pollMs = options.pollMs ?? SESSION_POLL_MS;

  // Keep the reader in a ref so the effect below does not re-subscribe when an
  // inline reader function identity changes between renders.
  const readerRef = useRef(readCookie);
  readerRef.current = readCookie;

  const [loggedIn, setLoggedIn] = useState<boolean>(() =>
    hasSessionCookie(readCookie()),
  );

  const refresh = useCallback(() => {
    setLoggedIn(hasSessionCookie(readerRef.current()));
  }, []);

  useEffect(() => {
    // Re-read immediately on mount (covers SSR/hydration and reader changes).
    refresh();

    const onFocus = () => refresh();
    const onVisibility = () => {
      if (
        typeof document === "undefined" ||
        document.visibilityState === "visible"
      ) {
        refresh();
      }
    };

    if (typeof window !== "undefined") {
      window.addEventListener("focus", onFocus);
      window.addEventListener("pageshow", onFocus);
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }

    let timer: ReturnType<typeof setInterval> | undefined;
    if (pollMs > 0) {
      timer = setInterval(refresh, pollMs);
    }

    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("focus", onFocus);
        window.removeEventListener("pageshow", onFocus);
      }
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
      if (timer) clearInterval(timer);
    };
  }, [refresh, pollMs]);

  return { loggedIn, refresh };
}
