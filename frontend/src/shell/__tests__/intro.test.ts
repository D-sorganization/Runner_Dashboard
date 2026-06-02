/**
 * Tests for the per-tab intro registry (#822).
 *
 * TDD: authored alongside `intro.ts`. Verifies copy is seeded from the nav
 * registry (DRY), that jargon-heavy admin tabs get expanded overrides, and the
 * DbC invariant that overrides reference real nav ids.
 */
import { describe, it, expect } from "vitest";
import { introForTab, INTRO_OVERRIDES, INTRO_OVERRIDE_IDS } from "../intro";
import { navItemById, NAV_ITEMS } from "../navRegistry";

describe("introForTab", () => {
  it("seeds the body from the nav registry tooltip when no override exists", () => {
    const intro = introForTab("queue");
    expect(intro).toBeDefined();
    expect(intro!.title).toBe(navItemById("queue")!.label);
    expect(intro!.body).toBe(navItemById("queue")!.tooltip);
  });

  it("returns undefined for an unknown tab", () => {
    expect(introForTab("does-not-exist")).toBeUndefined();
    expect(introForTab(undefined)).toBeUndefined();
  });

  it("expands the jargon-heavy Cline tab beyond its terse tooltip", () => {
    const intro = introForTab("cline-launcher");
    expect(intro!.body).toBe(INTRO_OVERRIDES["cline-launcher"]);
    // The expanded copy de-jargons "Cline".
    expect(intro!.body).toMatch(/AI coding-agent/i);
    expect(intro!.body).not.toBe(navItemById("cline-launcher")!.tooltip);
  });

  it("expands the 'principals' admin tab into operator English", () => {
    const intro = introForTab("principals");
    expect(intro!.body).toMatch(/identities/i);
    expect(intro!.body).toMatch(/acting on behalf/i);
  });
});

describe("INTRO_OVERRIDES (DbC)", () => {
  it("every override id references a real nav item", () => {
    for (const id of INTRO_OVERRIDE_IDS) {
      expect(NAV_ITEMS.some((it) => it.id === id)).toBe(true);
    }
  });
});
