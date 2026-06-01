// @vitest-environment node
/**
 * Tests for the nav registry — the single typed source of truth for every
 * navigable category in the dashboard shell (issue #797, part of #796).
 *
 * The registry feeds the sidebar (#798), the slim top toolstrip (#799), and
 * the grouped dropdowns (#800). These tests pin the Design-by-Contract
 * invariants that every shell surface relies on (DRY: one source, many views).
 */
import { describe, it, expect } from "vitest";
import {
  NAV_ITEMS,
  NAV_GROUPS,
  frequentItems,
  itemsByGroup,
  navItemById,
  assertValidNavRegistry,
  type NavItem,
  type NavGroupId,
} from "../navRegistry";

describe("nav registry — structure", () => {
  it("exposes a non-empty list of nav items", () => {
    expect(Array.isArray(NAV_ITEMS)).toBe(true);
    expect(NAV_ITEMS.length).toBeGreaterThan(0);
  });

  it("declares an ordered list of groups", () => {
    expect(Array.isArray(NAV_GROUPS)).toBe(true);
    expect(NAV_GROUPS.length).toBeGreaterThan(0);
    for (const g of NAV_GROUPS) {
      expect(typeof g.id).toBe("string");
      expect(typeof g.label).toBe("string");
      expect(g.label.length).toBeGreaterThan(0);
    }
  });
});

describe("nav registry — DbC invariants", () => {
  it("every item has the full typed contract", () => {
    for (const item of NAV_ITEMS) {
      expect(typeof item.id).toBe("string");
      expect(item.id.length).toBeGreaterThan(0);
      expect(typeof item.label).toBe("string");
      expect(item.label.length).toBeGreaterThan(0);
      // tooltip/description is mandatory — every nav item must explain itself.
      expect(typeof item.tooltip).toBe("string");
      expect(item.tooltip.length).toBeGreaterThan(0);
      expect(typeof item.frequent).toBe("boolean");
      // Icon is a renderable component (function).
      expect(typeof item.Icon).toBe("function");
      // tabId is the legacy App tab string this item activates.
      expect(typeof item.tabId).toBe("string");
      expect(item.tabId.length).toBeGreaterThan(0);
      // group must be one of the declared groups.
      const groupIds = NAV_GROUPS.map((g) => g.id);
      expect(groupIds).toContain(item.group);
    }
  });

  it("has no duplicate item ids", () => {
    const ids = NAV_ITEMS.map((i) => i.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("has no duplicate group ids", () => {
    const ids = NAV_GROUPS.map((g) => g.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("has no duplicate tabIds", () => {
    const tabIds = NAV_ITEMS.map((i) => i.tabId);
    expect(new Set(tabIds).size).toBe(tabIds.length);
  });

  it("marks at least one frequent item and not all of them", () => {
    const freq = NAV_ITEMS.filter((i) => i.frequent);
    expect(freq.length).toBeGreaterThanOrEqual(1);
    expect(freq.length).toBeLessThan(NAV_ITEMS.length);
  });

  it("includes the four expected top-bar categories as frequent", () => {
    const freqTabIds = NAV_ITEMS.filter((i) => i.frequent).map((i) => i.tabId);
    for (const expected of ["overview", "queue", "remediation", "conductor"]) {
      expect(freqTabIds).toContain(expected);
    }
  });

  it("every declared group is non-empty", () => {
    for (const g of NAV_GROUPS) {
      const items = NAV_ITEMS.filter((i) => i.group === g.id);
      expect(items.length).toBeGreaterThan(0);
    }
  });
});

describe("assertValidNavRegistry — contract enforcement", () => {
  it("passes for the real registry", () => {
    expect(() => assertValidNavRegistry(NAV_ITEMS, NAV_GROUPS)).not.toThrow();
  });

  it("throws when an item references an unknown group", () => {
    const bad: NavItem[] = [
      { ...NAV_ITEMS[0], id: "bad", tabId: "bad", group: "nope" as NavGroupId },
    ];
    expect(() => assertValidNavRegistry(bad, NAV_GROUPS)).toThrow();
  });

  it("throws on duplicate ids", () => {
    const dup: NavItem[] = [NAV_ITEMS[0], { ...NAV_ITEMS[0] }];
    expect(() => assertValidNavRegistry(dup, NAV_GROUPS)).toThrow();
  });

  it("throws on an empty tooltip", () => {
    const bad: NavItem[] = [{ ...NAV_ITEMS[0], tooltip: "" }];
    expect(() => assertValidNavRegistry(bad, NAV_GROUPS)).toThrow();
  });
});

describe("nav registry — selectors", () => {
  it("frequentItems returns only frequent items, preserving order", () => {
    const f = frequentItems();
    expect(f.every((i) => i.frequent)).toBe(true);
    const expectedOrder = NAV_ITEMS.filter((i) => i.frequent).map((i) => i.id);
    expect(f.map((i) => i.id)).toEqual(expectedOrder);
  });

  it("itemsByGroup groups every item under its declared group", () => {
    const grouped = itemsByGroup();
    let total = 0;
    for (const g of NAV_GROUPS) {
      const items = grouped[g.id] ?? [];
      total += items.length;
      expect(items.every((i) => i.group === g.id)).toBe(true);
    }
    expect(total).toBe(NAV_ITEMS.length);
  });

  it("itemsByGroup returns groups in declared order", () => {
    const grouped = itemsByGroup();
    expect(Object.keys(grouped)).toEqual(NAV_GROUPS.map((g) => g.id));
  });

  it("navItemById finds a known item and returns undefined otherwise", () => {
    const first = NAV_ITEMS[0];
    expect(navItemById(first.id)?.id).toBe(first.id);
    expect(navItemById("does-not-exist")).toBeUndefined();
  });
});
