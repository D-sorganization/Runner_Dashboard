/**
 * The Organization tab is retired (#1338, owner decision: fold into Projects,
 * which now shows per-repo CI status). Contract: no nav entry, and every old
 * address lands on Projects.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { INTRO_OVERRIDES } from "../intro";
import { getTabRedirect, pathnameToTabId } from "../routing";

const PROJECTS_TARGET = { to: "/work/projects", label: "Projects" };

describe("retired Organization tab (#1338)", () => {
  it("has no nav entry and no intro copy", () => {
    expect(navItemById("org")).toBeUndefined();
    expect(INTRO_OVERRIDES).not.toHaveProperty("org");
  });

  it.each(["/fleet/org", "/t/org", "/org", "/fleet/org/"])(
    "redirects %s to Projects",
    (path) => {
      expect(getTabRedirect(path)).toEqual(PROJECTS_TARGET);
    },
  );

  it.each(["/fleet/org", "/t/org", "/org"])("resolves %s to the Projects tab", (path) => {
    expect(pathnameToTabId(path)).toBe("projects");
  });
});
