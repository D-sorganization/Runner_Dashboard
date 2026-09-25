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
    expect(tabIdToPath("insights")).toBe("/fleet/insights");
    expect(pathnameToTabId("/fleet/insights")).toBe("insights");
    expect(tabIdToPath("deployment")).toBe("/fleet/deployment");
    expect(pathnameToTabId("/fleet/deployment")).toBe("deployment");
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

describe("SC-G4: Merge Reports and Analysis into Insights with redirects (issue #1326)", () => {
  it("maps insights to /fleet/insights and resolves back to insights", () => {
    expect(tabIdToPath("insights")).toBe("/fleet/insights");
    expect(pathnameToTabId("/fleet/insights")).toBe("insights");
  });

  it("redirects old tab routes /t/reports and /t/analysis to /fleet/insights", () => {
    const reportsRedirect = getTabRedirect("/t/reports");
    expect(reportsRedirect).toBeDefined();
    expect(reportsRedirect?.to).toBe("/fleet/insights");
    expect(reportsRedirect?.label).toBe("Insights");

    const analysisRedirect = getTabRedirect("/t/analysis");
    expect(analysisRedirect).toBeDefined();
    expect(analysisRedirect?.to).toBe("/fleet/insights");
    expect(analysisRedirect?.label).toBe("Insights");
  });

  it("redirects /fleet/reports and /fleet/analysis to /fleet/insights", () => {
    expect(getTabRedirect("/fleet/reports")?.to).toBe("/fleet/insights");
    expect(getTabRedirect("/fleet/analysis")?.to).toBe("/fleet/insights");
  });

  it("resolves /fleet/reports and /fleet/analysis to insights tabId", () => {
    expect(pathnameToTabId("/fleet/reports")).toBe("insights");
    expect(pathnameToTabId("/fleet/analysis")).toBe("insights");
  });
});

describe("SC-G2: One Fleet page: merge Machines, Runner Audit and Event Log into Fleet (issue #1324)", () => {
  it("redirects old tab routes /t/machines, /t/runner-audit and /t/events to /fleet sections", () => {
    const machinesRedirect = getTabRedirect("/t/machines");
    expect(machinesRedirect).toBeDefined();
    expect(machinesRedirect?.to).toBe("/fleet#machines");
    expect(machinesRedirect?.label).toBe("Machines");

    const auditRedirect = getTabRedirect("/t/runner-audit");
    expect(auditRedirect).toBeDefined();
    expect(auditRedirect?.to).toBe("/fleet#alerts");
    expect(auditRedirect?.label).toBe("Runner Audit");

    const eventsRedirect = getTabRedirect("/t/events");
    expect(eventsRedirect).toBeDefined();
    expect(eventsRedirect?.to).toBe("/fleet#events");
    expect(eventsRedirect?.label).toBe("Event Log");
  });

  it("redirects /fleet/machines, /fleet/runner-audit and /fleet/events to /fleet sections", () => {
    expect(getTabRedirect("/fleet/machines")?.to).toBe("/fleet#machines");
    expect(getTabRedirect("/fleet/runner-audit")?.to).toBe("/fleet#alerts");
    expect(getTabRedirect("/fleet/events")?.to).toBe("/fleet#events");
  });

  it("redirects /machines, /runner-audit and /events to /fleet sections", () => {
    expect(getTabRedirect("/machines")?.to).toBe("/fleet#machines");
    expect(getTabRedirect("/runner-audit")?.to).toBe("/fleet#alerts");
    expect(getTabRedirect("/events")?.to).toBe("/fleet#events");
  });

  it("resolves /fleet/machines, /fleet/runner-audit and /fleet/events to overview tabId", () => {
    expect(pathnameToTabId("/fleet/machines")).toBe("overview");
    expect(pathnameToTabId("/fleet/runner-audit")).toBe("overview");
    expect(pathnameToTabId("/fleet/events")).toBe("overview");
  });
});


