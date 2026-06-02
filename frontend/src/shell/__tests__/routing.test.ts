// @vitest-environment node
/**
 * Tests for routing.ts — the URL <-> nav tab mapping that makes every
 * navRegistry tab a real, deep-linkable route (issues #835, #831).
 *
 * These pin the Design-by-Contract invariants the shell relies on:
 *  - the mapping is total (every path resolves to a real tabId);
 *  - canonical paths round-trip (path -> tab -> path is stable);
 *  - the dedicated push-settings deep link is preserved;
 *  - legacy aliases still resolve so old bookmarks keep working.
 */
import { describe, it, expect } from "vitest"
import {
  DEFAULT_TAB_ID,
  PUSH_SETTINGS_PATH,
  isPushSettingsRoute,
  normalizeTabId,
  pathnameToTabId,
  tabIdToPath,
  allTabPaths,
} from "../routing"
import { NAV_ITEMS, navItemById } from "../navRegistry"

describe("routing — pathnameToTabId", () => {
  it("maps the root path to the default tab", () => {
    expect(pathnameToTabId("/")).toBe(DEFAULT_TAB_ID)
    expect(pathnameToTabId("")).toBe(DEFAULT_TAB_ID)
  })

  it("maps /t/<tabId> to that tab for every registry tab", () => {
    for (const item of NAV_ITEMS) {
      // push-settings has its own canonical path, asserted separately.
      if (item.tabId === "push-settings") continue
      expect(pathnameToTabId(`/t/${item.tabId}`)).toBe(item.tabId)
    }
  })

  it("maps the push-settings deep link to the push-settings tab", () => {
    expect(pathnameToTabId(PUSH_SETTINGS_PATH)).toBe("push-settings")
    expect(pathnameToTabId("/settings/push/")).toBe("push-settings")
  })

  it("falls back to the default tab for unknown routes", () => {
    expect(pathnameToTabId("/does/not/exist")).toBe(DEFAULT_TAB_ID)
    expect(pathnameToTabId("/t/not-a-real-tab")).toBe(DEFAULT_TAB_ID)
  })

  it("tolerates a trailing slash on tab routes", () => {
    expect(pathnameToTabId("/t/queue/")).toBe("queue")
  })

  it("always returns a real registry tabId (totality)", () => {
    for (const p of ["/", "/t/queue", "/settings/push", "/garbage", "/t/x"]) {
      expect(navItemById(pathnameToTabId(p))).toBeDefined()
    }
  })
})

describe("routing — tabIdToPath", () => {
  it("maps the default tab to the root path", () => {
    expect(tabIdToPath(DEFAULT_TAB_ID)).toBe("/")
  })

  it("maps push-settings to its dedicated deep link", () => {
    expect(tabIdToPath("push-settings")).toBe(PUSH_SETTINGS_PATH)
  })

  it("maps any other tab to /t/<tabId>", () => {
    expect(tabIdToPath("queue")).toBe("/t/queue")
    expect(tabIdToPath("maxwell")).toBe("/t/maxwell")
  })
})

describe("routing — round-trip", () => {
  it("path -> tab -> path is stable for every canonical tab path", () => {
    for (const item of NAV_ITEMS) {
      const path = tabIdToPath(item.tabId)
      const tab = pathnameToTabId(path)
      expect(tabIdToPath(tab)).toBe(path)
    }
  })

  it("allTabPaths covers every registry item", () => {
    expect(allTabPaths()).toHaveLength(NAV_ITEMS.length)
  })
})

describe("routing — aliases and push detection", () => {
  it("normalizes legacy aliases to canonical tabIds", () => {
    expect(normalizeTabId("fleet")).toBe("overview")
    expect(normalizeTabId("health")).toBe("queue")
    expect(normalizeTabId("queue")).toBe("queue")
  })

  it("detects the push-settings route (with/without trailing slash)", () => {
    expect(isPushSettingsRoute("/settings/push")).toBe(true)
    expect(isPushSettingsRoute("/settings/push/")).toBe(true)
    expect(isPushSettingsRoute("/")).toBe(false)
    expect(isPushSettingsRoute("/t/queue")).toBe(false)
  })
})
