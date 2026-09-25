// @vitest-environment node
/**
 * Tests for the old tab redirect table and SC-D2 route restructuring (issue #1309).
 *
 * Requirements:
 *  - Default route "/" maps to Staff Console ("staff").
 *  - Every old tab ID in navRegistry resolves to a canonical URL path via redirect table.
 *  - Legacy aliases (fleet, health, work) are preserved.
 *  - The four areas are Staff ("/"), Work ("/work"), Fleet ("/fleet"), Settings ("/settings").
 *  - Secondary pages live under their respective area prefix.
 */
import { describe, it, expect } from "vitest";
import { NAV_ITEMS } from "../navRegistry";
import {
  DEFAULT_TAB_ID,
  REDIRECT_TABLE,
  getTabRedirect,
  tabIdToPath,
  pathnameToTabId,
} from "../routing";

describe("SC-D2: Default route is Staff Console", () => {
  it("DEFAULT_TAB_ID is 'staff'", () => {
    expect(DEFAULT_TAB_ID).toBe("staff");
  });

  it("pathnameToTabId('/') returns 'staff'", () => {
    expect(pathnameToTabId("/")).toBe("staff");
    expect(pathnameToTabId("")).toBe("staff");
  });

  it("tabIdToPath('staff') returns '/'", () => {
    expect(tabIdToPath("staff")).toBe("/");
  });
});

describe("SC-D2: Four areas canonical routes", () => {
  it("maps four area roots to their canonical tabs", () => {
    expect(pathnameToTabId("/")).toBe("staff");
    expect(pathnameToTabId("/work")).toBe("queue");
    expect(pathnameToTabId("/fleet")).toBe("overview");
    expect(pathnameToTabId("/settings")).toBe("settings");
  });

  it("maps four area canonical tabs to their paths", () => {
    expect(tabIdToPath("staff")).toBe("/");
    expect(tabIdToPath("queue")).toBe("/work");
    expect(tabIdToPath("overview")).toBe("/fleet");
    expect(tabIdToPath("settings")).toBe("/settings");
  });

  it("preserves dedicated push settings path", () => {
    expect(tabIdToPath("push-settings")).toBe("/settings/push");
    expect(pathnameToTabId("/settings/push")).toBe("push-settings");
  });
});

describe("SC-D2: Redirect table covers every nav item", () => {
  it("has a redirect entry for every registry tabId from /t/<tabId>", () => {
    for (const item of NAV_ITEMS) {
      const redirect = getTabRedirect(`/t/${item.tabId}`);
      expect(redirect).toBeDefined();
      expect(redirect?.to).toBe(tabIdToPath(item.tabId));
      expect(redirect?.label).toBe(item.label);
    }
  });

  it("redirects legacy aliases /t/fleet, /t/health, /t/work", () => {
    expect(getTabRedirect("/t/fleet")?.to).toBe("/fleet");
    expect(getTabRedirect("/t/health")?.to).toBe("/work");
    expect(getTabRedirect("/t/work")?.to).toBe("/work");
  });

  it("REDIRECT_TABLE contains all mapped entries", () => {
    expect(Object.keys(REDIRECT_TABLE).length).toBeGreaterThanOrEqual(NAV_ITEMS.length);
    for (const item of NAV_ITEMS) {
      expect(REDIRECT_TABLE[item.tabId]).toBeDefined();
    }
  });

  it("every redirect destination resolves to a valid tabId via pathnameToTabId", () => {
    for (const item of NAV_ITEMS) {
      const targetPath = tabIdToPath(item.tabId);
      const resolvedTabId = pathnameToTabId(targetPath);
      expect(resolvedTabId).toBe(item.tabId);
    }
  });
});

describe("SC-D2: Secondary pages live under their area prefix", () => {
  it("maps secondary fleet pages under /fleet/*", () => {
    expect(tabIdToPath("machines")).toBe("/fleet/machines");
    expect(pathnameToTabId("/fleet/machines")).toBe("machines");
    expect(tabIdToPath("events")).toBe("/fleet/events");
    expect(pathnameToTabId("/fleet/events")).toBe("events");
  });

  it("maps secondary work pages under /work/*", () => {
    expect(tabIdToPath("remediation")).toBe("/work/remediation");
    expect(pathnameToTabId("/work/remediation")).toBe("remediation");
    expect(tabIdToPath("workflows")).toBe("/work/workflows");
    expect(pathnameToTabId("/work/workflows")).toBe("workflows");
  });

  it("maps secondary staff pages under /staff/*", () => {
    expect(tabIdToPath("fleet-command")).toBe("/staff/fleet-command");
    expect(pathnameToTabId("/staff/fleet-command")).toBe("fleet-command");
    expect(tabIdToPath("maxwell")).toBe("/staff/maxwell");
    expect(pathnameToTabId("/staff/maxwell")).toBe("maxwell");
  });

  it("maps secondary settings pages under /settings/*", () => {
    expect(tabIdToPath("credentials")).toBe("/settings/credentials");
    expect(pathnameToTabId("/settings/credentials")).toBe("credentials");
    expect(tabIdToPath("linear-setup")).toBe("/settings/linear-setup");
    expect(pathnameToTabId("/settings/linear-setup")).toBe("linear-setup");
  });
});
