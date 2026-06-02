/**
 * routing.ts — the single source of truth that maps between the browser URL
 * and the dashboard's active nav tab (issues #835, #831).
 *
 * `main.tsx` mounts a React Router `<BrowserRouter>` and the shell derives its
 * active tab purely from the URL via these helpers, so every `navRegistry`
 * tab is a real, deep-linkable route: operators can bookmark/share a tab and
 * browser back/forward traverse tabs. This retires the previous hand-rolled
 * `window.location.pathname` navigation that kept the active tab in React
 * state (not in the URL).
 *
 * Law of Demeter: callers pass and receive flat strings — no reaching into
 * router internals. Design by Contract: the mapping is total (every tabId has
 * a canonical path, every path resolves to a tabId) and round-trips for the
 * canonical form (`tabIdToPath(pathnameToTabId(p))` is stable).
 */
import { NAV_ITEMS, navItemById } from "./navRegistry"

/** The default landing tab when the URL carries no explicit tab. */
export const DEFAULT_TAB_ID = "overview"

/** Canonical pathname for the dedicated push-settings deep link. */
export const PUSH_SETTINGS_PATH = "/settings/push"

/** The legacy tabId for push settings within the nav registry. */
const PUSH_SETTINGS_TAB_ID = "push-settings"

/**
 * Legacy tabId aliases normalized to their canonical registry tabId. These
 * mirror historical query-string / mobile aliases so old bookmarks resolve.
 */
const TAB_ID_ALIASES: Record<string, string> = {
  fleet: "overview",
  health: "queue",
}

/** Normalize a possibly-aliased tabId to its canonical registry tabId. */
export function normalizeTabId(tabId: string): string {
  return TAB_ID_ALIASES[tabId] ?? tabId
}

/** Strip any trailing slashes (but keep the root "/"). */
function stripTrailingSlash(pathname: string): string {
  return pathname.replace(/\/+$/, "") || "/"
}

/** True when the pathname is the dedicated push-settings route. */
export function isPushSettingsRoute(pathname: string): boolean {
  return stripTrailingSlash(pathname) === PUSH_SETTINGS_PATH
}

/**
 * Map a browser pathname to a canonical nav tabId.
 *
 * Routes:
 *  - "/"                  -> DEFAULT_TAB_ID (overview)
 *  - "/settings/push"     -> "push-settings"
 *  - "/t/<tabId>"         -> "<tabId>" (normalized; unknown ids fall back)
 *  - anything else        -> DEFAULT_TAB_ID
 *
 * Postcondition: the returned id is always a real registry tabId.
 */
export function pathnameToTabId(pathname: string): string {
  const normalized = stripTrailingSlash(pathname)

  if (normalized === "/") return DEFAULT_TAB_ID
  if (normalized === PUSH_SETTINGS_PATH) return PUSH_SETTINGS_TAB_ID

  const tabMatch = normalized.match(/^\/t\/([^/]+)$/)
  if (tabMatch) {
    const candidate = normalizeTabId(decodeURIComponent(tabMatch[1]))
    if (navItemById(candidate)) return candidate
  }

  return DEFAULT_TAB_ID
}

/**
 * Map a nav tabId to its canonical, bookmarkable pathname.
 *
 * Precondition: `tabId` should be a known registry tabId; unknown ids still
 * produce a syntactically valid `/t/<id>` path so navigation never throws.
 */
export function tabIdToPath(tabId: string): string {
  const canonical = normalizeTabId(tabId)
  if (canonical === DEFAULT_TAB_ID) return "/"
  if (canonical === PUSH_SETTINGS_TAB_ID) return PUSH_SETTINGS_PATH
  return `/t/${encodeURIComponent(canonical)}`
}

/** Every canonical tab path, in registry order — handy for tests/preloading. */
export function allTabPaths(): string[] {
  return NAV_ITEMS.map((it) => tabIdToPath(it.tabId))
}
