/**
 * Local Tools is a section of Operations (#1338, owner decision 2026-10-23).
 * Contract: no nav entry or intro copy of its own, and every old address lands
 * on the Local Tools section of the Operations page.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { INTRO_OVERRIDES } from "../intro";
import { getTabRedirect, pathnameToTabId } from "../routing";

const LOCAL_TOOLS_TARGET = {
  to: "/fleet/operations#local-tools",
  label: "Local Tools (Operations)",
};

describe("Local Tools under Operations (#1338)", () => {
  it("has no nav entry and no intro copy", () => {
    expect(navItemById("local-apps")).toBeUndefined();
    expect(INTRO_OVERRIDES).not.toHaveProperty("local-apps");
  });

  it.each(["/settings/local-apps", "/t/local-apps", "/local-apps", "/settings/local-apps/"])(
    "redirects %s to the Local Tools section of Operations",
    (path) => {
      expect(getTabRedirect(path)).toEqual(LOCAL_TOOLS_TARGET);
    },
  );

  it.each(["/settings/local-apps", "/t/local-apps", "/local-apps"])(
    "resolves %s to the Operations tab",
    (path) => {
      expect(pathnameToTabId(path)).toBe("operations");
    },
  );
});
