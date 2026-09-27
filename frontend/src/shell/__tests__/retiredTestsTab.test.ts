/**
 * The Tests page moved under Operations → Diagnostics (#1338, owner decision).
 * Contract: it has no top-level nav entry, and every old address lands on the
 * Tests subsection of the Diagnostics section of the Operations page.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { getTabRedirect, pathnameToTabId } from "../routing";

const TESTS_TARGET = { to: "/fleet/operations#tests", label: "Tests (Diagnostics)" };

describe("Tests moved under Operations → Diagnostics (#1338)", () => {
  it("has no top-level nav entry", () => {
    expect(navItemById("tests")).toBeUndefined();
  });

  it.each(["/t/tests", "/settings/tests", "/tests", "/settings/tests/"])(
    "redirects %s to the Diagnostics tests subsection",
    (path) => {
      expect(getTabRedirect(path)).toEqual(TESTS_TARGET);
    },
  );

  it.each(["/t/tests", "/settings/tests", "/tests"])(
    "resolves %s to the Operations tab",
    (path) => {
      expect(pathnameToTabId(path)).toBe("operations");
    },
  );
});
