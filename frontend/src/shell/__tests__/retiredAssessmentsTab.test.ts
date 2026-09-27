/**
 * The Assessments tab is retired (#1338, owner decision: "assess repo X" is a
 * request on the Projects card that runs the existing assessment workflow, and
 * score history moves onto the Projects card). Contract: no nav entry, and
 * every old address lands on Projects.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { INTRO_OVERRIDES } from "../intro";
import { REDIRECT_TABLE, getTabRedirect, pathnameToTabId } from "../routing";

const PROJECTS_TARGET = { to: "/work/projects", label: "Projects" };

describe("retired Assessments tab (#1338)", () => {
  it("has no nav entry and no intro copy", () => {
    expect(navItemById("assessments")).toBeUndefined();
    expect(INTRO_OVERRIDES).not.toHaveProperty("assessments");
  });

  it("maps the old tab id to Projects", () => {
    expect(REDIRECT_TABLE["assessments"]).toEqual(PROJECTS_TARGET);
  });

  it.each(["/fleet/assessments", "/t/assessments", "/assessments", "/fleet/assessments/"])(
    "redirects %s to Projects",
    (path) => {
      expect(getTabRedirect(path)).toEqual(PROJECTS_TARGET);
    },
  );

  it.each(["/fleet/assessments", "/t/assessments", "/assessments"])(
    "resolves %s to the Projects tab",
    (path) => {
      expect(pathnameToTabId(path)).toBe("projects");
    },
  );
});
