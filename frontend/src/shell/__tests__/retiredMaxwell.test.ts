/**
 * The Maxwell dashboard tab is retired (#1338). Contract: no nav entry, and
 * every old address lands on the Staff Console instead of a blank page or 404.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { INTRO_OVERRIDES } from "../intro";
import { getTabRedirect } from "../routing";

describe("retired Maxwell tab (#1338)", () => {
  it("has no nav entry and no intro override", () => {
    expect(navItemById("maxwell")).toBeUndefined();
    expect(INTRO_OVERRIDES).not.toHaveProperty("maxwell");
  });

  it.each([
    "/staff/maxwell",
    "/maxwell",
    "/t/maxwell",
    "/staff/maxwell/",
  ])("redirects %s to the Staff Console", (path) => {
    expect(getTabRedirect(path)).toEqual({ to: "/", label: "Staff Console" });
  });
});
