/**
 * routing.ts — Single source of truth that maps between the browser URL
 * and the dashboard's active nav tab (SC-D2 / issue #1309).
 *
 * Requirements:
 *  - Default route "/" maps to Staff Console ("staff").
 *  - The four areas have canonical roots:
 *      * Staff: "/"
 *      * Work: "/work" (maps to "queue")
 *      * Fleet: "/fleet" (maps to "overview")
 *      * Settings: "/settings" (maps to "settings")
 *  - Secondary pages live under their area prefix:
 *      * /fleet/:tabId, /work/:tabId, /staff/:tabId, /settings/:tabId
 *  - Old /t/:tabId routes redirect to the canonical path with a one-time toast.
 *  - Unknown routes resolve to undefined, rendering the NotFoundPanel.
 */
import { NAV_ITEMS, navItemById } from "./navRegistry";

/** The default landing tab when the URL carries no explicit tab (SC-D2). */
export const DEFAULT_TAB_ID = "staff";

/** Canonical pathname for the dedicated push-settings deep link. */
export const PUSH_SETTINGS_PATH = "/settings/push";

/** The legacy tabId for push settings within the nav registry. */
export const PUSH_SETTINGS_TAB_ID = "push-settings";

/**
 * Legacy tabId aliases normalized to their canonical registry tabId. These
 * mirror historical query-string / mobile aliases so old bookmarks resolve.
 */
const TAB_ID_ALIASES: Record<string, string> = {
  fleet: "overview",
  health: "queue",
  work: "queue",
  reports: "insights",
  analysis: "insights",
};

/** Normalize a possibly-aliased tabId to its canonical registry tabId. */
export function normalizeTabId(tabId: string): string {
  return TAB_ID_ALIASES[tabId] ?? tabId;
}

/** Strip any trailing slashes (but keep the root "/"). */
function stripTrailingSlash(pathname: string): string {
  return pathname.replace(/\/+$/, "") || "/";
}

/** True when the pathname is the dedicated push-settings route. */
export function isPushSettingsRoute(pathname: string): boolean {
  return stripTrailingSlash(pathname) === PUSH_SETTINGS_PATH;
}

/**
 * Map a nav tabId to its canonical, bookmarkable pathname.
 *
 * In SC-D2:
 *  - "staff" -> "/"
 *  - "queue" -> "/work"
 *  - "overview" -> "/fleet"
 *  - "settings" -> "/settings"
 *  - "push-settings" -> "/settings/push"
 *  - Secondary pages -> "/<group>/<tabId>"
 */
export function tabIdToPath(tabId: string): string {
  const canonical = normalizeTabId(tabId);
  if (canonical === DEFAULT_TAB_ID) return "/";
  if (canonical === "queue") return "/work";
  if (canonical === "overview") return "/fleet";
  if (canonical === "settings") return "/settings";
  if (canonical === PUSH_SETTINGS_TAB_ID) return PUSH_SETTINGS_PATH;

  const item = navItemById(canonical);
  if (item) {
    return `/${item.group}/${encodeURIComponent(canonical)}`;
  }
  return `/t/${encodeURIComponent(canonical)}`;
}

export interface RedirectTarget {
  to: string;
  label: string;
}

/**
 * Static redirect table mapping old tabIds and legacy aliases to their
 * new canonical routes and labels.
 */
export const REDIRECT_TABLE: Record<string, RedirectTarget> = (() => {
  const table: Record<string, RedirectTarget> = {};
  for (const item of NAV_ITEMS) {
    table[item.tabId] = {
      to: tabIdToPath(item.tabId),
      label: item.label,
    };
  }
  // Explicit aliases
  table["fleet"] = { to: "/fleet", label: "Fleet" };
  table["health"] = { to: "/work", label: "Queue" };
  table["work"] = { to: "/work", label: "Work" };
  table["push-settings"] = { to: PUSH_SETTINGS_PATH, label: "Notifications" };
  table["reports"] = { to: "/fleet/insights", label: "Insights" };
  table["analysis"] = { to: "/fleet/insights", label: "Insights" };
  table["machines"] = { to: "/fleet#machines", label: "Machines" };
  table["runner-audit"] = { to: "/fleet#alerts", label: "Runner Audit" };
  table["events"] = { to: "/fleet#events", label: "Event Log" };
  return table;
})();

/**
 * Determine if a pathname is an old tab route (/t/<tabId>), and return
 * the redirect target if known.
 */
export function getTabRedirect(pathname: string): RedirectTarget | null {
  const normalized = stripTrailingSlash(pathname);
  if (normalized === "/fleet/reports" || normalized === "/fleet/analysis") {
    return { to: "/fleet/insights", label: "Insights" };
  }
  if (normalized === "/fleet/machines" || normalized === "/machines") {
    return { to: "/fleet#machines", label: "Machines" };
  }
  if (normalized === "/fleet/runner-audit" || normalized === "/runner-audit") {
    return { to: "/fleet#alerts", label: "Runner Audit" };
  }
  if (normalized === "/fleet/events" || normalized === "/events") {
    return { to: "/fleet#events", label: "Event Log" };
  }
  const match = normalized.match(/^\/t\/([^/]+)$/);
  if (!match) return null;
  const rawId = decodeURIComponent(match[1]);
  if (REDIRECT_TABLE[rawId]) {
    return REDIRECT_TABLE[rawId];
  }
  const canonical = normalizeTabId(rawId);
  if (REDIRECT_TABLE[canonical]) {
    return REDIRECT_TABLE[canonical];
  }
  const item = navItemById(canonical);
  if (item) {
    return {
      to: tabIdToPath(item.tabId),
      label: item.label,
    };
  }
  return null;
}

/**
 * Map a browser pathname to a canonical nav tabId.
 *
 * Routes recognized:
 *  - "/" | ""             -> "staff" (DEFAULT_TAB_ID)
 *  - "/staff"             -> "staff"
 *  - "/work"              -> "queue"
 *  - "/fleet"             -> "overview"
 *  - "/settings"          -> "settings"
 *  - "/settings/push"     -> "push-settings"
 *  - "/fleet/:tabId"      -> tabId (if in fleet group)
 *  - "/work/:tabId"       -> tabId (if in work group)
 *  - "/staff/:tabId"      -> tabId (if in staff group)
 *  - "/settings/:tabId"   -> tabId (if in settings group)
 *  - "/t/:tabId"          -> tabId (for backwards compatibility)
 *
 * Returns undefined for unknown routes so shell can render NotFoundPanel.
 */
export function pathnameToTabId(pathname: string): string | undefined {
  const normalized = stripTrailingSlash(pathname);

  if (normalized === "/" || normalized === "") return DEFAULT_TAB_ID;
  if (normalized === "/staff") return DEFAULT_TAB_ID;
  if (normalized === "/work") return "queue";
  if (normalized === "/fleet") return "overview";
  if (normalized === "/settings") return "settings";
  if (normalized === PUSH_SETTINGS_PATH) return PUSH_SETTINGS_TAB_ID;

  if (normalized === "/fleet/reports" || normalized === "/fleet/analysis") {
    return "insights";
  }
  if (
    normalized === "/fleet/machines" ||
    normalized === "/machines" ||
    normalized === "/fleet/runner-audit" ||
    normalized === "/runner-audit" ||
    normalized === "/fleet/events" ||
    normalized === "/events"
  ) {
    return "overview";
  }

  // Secondary area pages: /<group>/<tabId>
  const areaMatch = normalized.match(/^\/(fleet|work|staff|settings)\/([^/]+)$/);
  if (areaMatch) {
    const rawId = decodeURIComponent(areaMatch[2]);
    const candidate = normalizeTabId(rawId);
    const item = navItemById(candidate);
    if (item && item.group === areaMatch[1]) {
      return item.tabId;
    }
  }

  // Old tab routes: /t/<tabId>
  const tabMatch = normalized.match(/^\/t\/([^/]+)$/);
  if (tabMatch) {
    const candidate = normalizeTabId(decodeURIComponent(tabMatch[1]));
    const item = navItemById(candidate);
    if (item) return item.tabId;
  }

  return undefined;
}

/** Every canonical tab path, in registry order — handy for tests/preloading. */
export function allTabPaths(): string[] {
  return NAV_ITEMS.map((it) => tabIdToPath(it.tabId));
}
