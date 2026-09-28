/**
 * Settings consolidation (#1338, owner decision): Credentials, Linear Setup,
 * Notifications and Principals are sections of the one Settings page, not nav
 * tabs. Contract: no nav entries or intro copy of their own, every old address
 * lands on the matching Settings section, and in-app links to those tab ids
 * go straight to the section.
 */
import { describe, expect, it } from "vitest";
import { NAV_ITEMS, navItemById } from "../navRegistry";
import { INTRO_OVERRIDES } from "../intro";
import { getTabRedirect, pathnameToTabId, tabIdToPath } from "../routing";

const SECTIONS = [
  { tabId: "credentials", section: "credentials", label: "Credentials" },
  { tabId: "linear-setup", section: "linear-setup", label: "Linear Setup" },
  { tabId: "push-settings", section: "notifications", label: "Notifications" },
  { tabId: "principals", section: "principals", label: "Principals" },
] as const;

describe("Settings consolidation (#1338)", () => {
  it.each(SECTIONS)("$tabId has no nav entry and no intro copy", ({ tabId }) => {
    expect(navItemById(tabId)).toBeUndefined();
    expect(INTRO_OVERRIDES).not.toHaveProperty(tabId);
  });

  it("leaves the Settings page as the only item of the Settings group", () => {
    const items = NAV_ITEMS.filter((item) => item.group === "settings");
    expect(items.map((item) => item.tabId)).toEqual(["settings"]);
  });

  it.each(
    SECTIONS.flatMap(({ tabId, section, label }) =>
      [`/settings/${tabId}`, `/t/${tabId}`, `/${tabId}`, `/settings/${tabId}/`].map(
        (path) => [path, section, label] as const,
      ),
    ),
  )("redirects %s to the %s section of Settings", (path, section, label) => {
    expect(getTabRedirect(path)).toEqual({
      to: `/settings#${section}`,
      label: `${label} (Settings)`,
    });
  });

  it("redirects the old push deep link to the Notifications section", () => {
    expect(getTabRedirect("/settings/push")).toEqual({
      to: "/settings#notifications",
      label: "Notifications (Settings)",
    });
    expect(pathnameToTabId("/settings/push")).toBe("settings");
  });

  it.each(SECTIONS)("resolves the old addresses of $tabId to the Settings tab", ({ tabId }) => {
    for (const path of [`/settings/${tabId}`, `/t/${tabId}`, `/${tabId}`]) {
      expect(pathnameToTabId(path)).toBe("settings");
    }
  });

  it.each(SECTIONS)("links tab id $tabId straight to its section", ({ tabId, section }) => {
    expect(tabIdToPath(tabId)).toBe(`/settings#${section}`);
  });
});
