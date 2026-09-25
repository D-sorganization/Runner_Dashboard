/**
 * The Cline Launcher is retired (#1338, owner decision; superseded by the
 * Staff Console). Contract: it has no nav entry, and every old address lands
 * on the Staff Console instead of a blank page or a 404.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { INTRO_OVERRIDES } from "../intro";
import { getTabRedirect } from "../routing";

describe("retired Cline Launcher (#1338)", () => {
  it("has no nav entry and no intro copy", () => {
    expect(navItemById("cline-launcher")).toBeUndefined();
    expect(INTRO_OVERRIDES).not.toHaveProperty("cline-launcher");
  });

  it.each(["/staff/cline-launcher", "/cline-launcher", "/t/cline-launcher", "/staff/cline-launcher/"])(
    "redirects %s to the Staff Console",
    (path) => {
      expect(getTabRedirect(path)).toEqual({ to: "/", label: "Staff Console" });
    },
  );
});
