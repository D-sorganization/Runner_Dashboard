// @vitest-environment node
/**
 * Tests for routing.ts — URL <-> nav tab mapping (updated for SC-D2 issue #1309).
 *
 * Contract:
 *  - Default route "/" maps to Staff Console ("staff").
 *  - The four areas have canonical roots:
 *      * Staff: "/"
 *      * Work: "/work" -> "queue"
 *      * Fleet: "/fleet" -> "overview"
 *      * Settings: "/settings" -> "settings"
 *  - Secondary pages live under their area prefix (/fleet/machines, etc.).
 *  - Canonical paths round-trip stably.
 *  - Unknown routes resolve to undefined (so shell can render NotFoundPanel).
 *  - Legacy aliases still resolve.
 */
import { describe, it, expect } from "vitest";
import {
  DEFAULT_TAB_ID,
  PUSH_SETTINGS_PATH,
  isPushSettingsRoute,
  normalizeTabId,
  pathnameToTabId,
  tabIdToPath,
  allTabPaths,
} from "../routing";
import { NAV_ITEMS, navItemById } from "../navRegistry";

describe("routing — pathnameToTabId", () => {
  it("maps the root path to the default tab (staff)", () => {
    expect(pathnameToTabId("/")).toBe(DEFAULT_TAB_ID);
    expect(pathnameToTabId("")).toBe(DEFAULT_TAB_ID);
    expect(DEFAULT_TAB_ID).toBe("staff");
  });

  it("maps four area root paths", () => {
    expect(pathnameToTabId("/work")).toBe("queue");
    expect(pathnameToTabId("/fleet")).toBe("overview");
    expect(pathnameToTabId("/settings")).toBe("settings");
  });

  it("maps /t/<tabId> to that tab for every registry tab (back-compat)", () => {
    for (const item of NAV_ITEMS) {
      if (item.tabId === "push-settings") continue;
      expect(pathnameToTabId(`/t/${item.tabId}`)).toBe(item.tabId);
    }
  });

  it("maps the push-settings deep link to the push-settings tab", () => {
    expect(pathnameToTabId(PUSH_SETTINGS_PATH)).toBe("push-settings");
    expect(pathnameToTabId("/settings/push/")).toBe("push-settings");
  });

  it("returns undefined for unknown routes to trigger not-found panel", () => {
    expect(pathnameToTabId("/does/not/exist")).toBeUndefined();
    expect(pathnameToTabId("/t/not-a-real-tab")).toBeUndefined();
    expect(pathnameToTabId("/garbage")).toBeUndefined();
  });

  it("tolerates a trailing slash on tab routes", () => {
    expect(pathnameToTabId("/t/queue/")).toBe("queue");
    expect(pathnameToTabId("/work/")).toBe("queue");
    expect(pathnameToTabId("/fleet/")).toBe("overview");
  });

  it("returns a valid registry tabId for known canonical routes", () => {
    for (const p of ["/", "/work", "/fleet", "/settings", "/settings/push"]) {
      const tabId = pathnameToTabId(p);
      expect(tabId).toBeDefined();
      expect(navItemById(tabId!)).toBeDefined();
    }
  });
});

describe("routing — tabIdToPath", () => {
  it("maps the default tab to the root path", () => {
    expect(tabIdToPath("staff")).toBe("/");
  });

  it("maps push-settings to its dedicated deep link", () => {
    expect(tabIdToPath("push-settings")).toBe(PUSH_SETTINGS_PATH);
  });

  it("maps area roots and secondary pages to canonical paths", () => {
    expect(tabIdToPath("queue")).toBe("/work");
    expect(tabIdToPath("overview")).toBe("/fleet");
    expect(tabIdToPath("settings")).toBe("/settings");
    expect(tabIdToPath("insights")).toBe("/fleet/insights");
    expect(tabIdToPath("maxwell")).toBe("/staff/maxwell");
    expect(tabIdToPath("remediation")).toBe("/work/remediation");
    expect(tabIdToPath("credentials")).toBe("/settings/credentials");
  });
});

describe("routing — round-trip", () => {
  it("path -> tab -> path is stable for every canonical tab path", () => {
    for (const item of NAV_ITEMS) {
      const path = tabIdToPath(item.tabId);
      const tab = pathnameToTabId(path);
      expect(tab).toBeDefined();
      expect(tabIdToPath(tab!)).toBe(path);
    }
  });

  it("allTabPaths covers every registry item", () => {
    expect(allTabPaths()).toHaveLength(NAV_ITEMS.length);
  });
});

describe("routing — aliases and push detection", () => {
  it("normalizes legacy aliases to canonical tabIds", () => {
    expect(normalizeTabId("fleet")).toBe("overview");
    expect(normalizeTabId("health")).toBe("queue");
    expect(normalizeTabId("work")).toBe("queue");
  });

  it("detects the push-settings route (with/without trailing slash)", () => {
    expect(isPushSettingsRoute("/settings/push")).toBe(true);
    expect(isPushSettingsRoute("/settings/push/")).toBe(true);
    expect(isPushSettingsRoute("/")).toBe(false);
    expect(isPushSettingsRoute("/t/queue")).toBe(false);
  });
});
