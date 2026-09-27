/**
 * Local Tools became a section of Settings (#1338, owner decision). Contract:
 * no nav entry or intro copy of its own, and every old address lands on the
 * Local Tools section of the Settings page.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { INTRO_OVERRIDES } from "../intro";
import { getTabRedirect, pathnameToTabId } from "../routing";

const LOCAL_TOOLS_TARGET = { to: "/settings#local-tools", label: "Local Tools (Settings)" };

describe("Local Tools moved into Settings (#1338)", () => {
  it("has no nav entry and no intro copy", () => {
    expect(navItemById("local-apps")).toBeUndefined();
    expect(INTRO_OVERRIDES).not.toHaveProperty("local-apps");
  });

  it.each(["/settings/local-apps", "/t/local-apps", "/local-apps", "/settings/local-apps/"])(
    "redirects %s to the Local Tools section of Settings",
    (path) => {
      expect(getTabRedirect(path)).toEqual(LOCAL_TOOLS_TARGET);
    },
  );

  it.each(["/settings/local-apps", "/t/local-apps", "/local-apps"])(
    "resolves %s to the Settings tab",
    (path) => {
      expect(pathnameToTabId(path)).toBe("settings");
    },
  );
});
