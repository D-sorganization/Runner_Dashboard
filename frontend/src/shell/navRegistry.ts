/**
 * navRegistry.ts — the single typed source of truth for every navigable
 * category in the dashboard (issue #797, updated for SC-D2 #1309).
 *
 * Every shell surface renders from this one registry (DRY):
 *   - the left sidebar (Sidebar.tsx), grouped by the 4 areas;
 *   - the command palette (CommandPalette.tsx) replacing the old top toolstrip;
 *   - the mobile bottom bar (MobileShell.tsx) with Staff, Work, Fleet, More.
 *
 * Design by Contract: `assertValidNavRegistry` encodes the invariants every
 * consumer relies on (unique ids, non-empty tooltips, valid group refs,
 * non-empty groups). It runs once at module load so a malformed registry
 * fails fast and loudly rather than silently producing a broken nav.
 *
 * Law of Demeter: consumers receive flat, typed `NavItem` records — no
 * reaching through nested objects. Selectors (`frequentItems`,
 * `itemsByGroup`, `navItemById`) keep view code declarative.
 */
import type { NavIcon } from "./navIcons";
import {
  NAV_GROUPS,
  NAV_ITEMS,
  type NavGroupId,
  type NavGroup,
  type NavItem,
} from "./navRegistryData";

export type { NavGroupId, NavGroup, NavItem };
export { NAV_GROUPS, NAV_ITEMS };

// ── Design-by-Contract validation ─────────────────────────────────────────

/**
 * Validate the registry's structural invariants. Throws on any violation.
 *
 * Preconditions enforced:
 *  - every item has non-empty id, label, tooltip, and tabId;
 *  - ids and tabIds are unique;
 *  - icons are unique (one distinct glyph per category — issue #840);
 *  - group ids are unique;
 *  - every item.group references a declared group;
 *  - every declared group has at least one item;
 *  - at least one (but not all) items are `frequent`;
 *  - `mobilePrimary` and `mobileDrawer` are mutually exclusive booleans, and at
 *    least one (but not all) items are `mobilePrimary` (issue #821).
 *
 * Postcondition: if this returns, all consumers may assume the above hold.
 */
export function assertValidNavRegistry(
  items: readonly NavItem[],
  groups: readonly NavGroup[],
): void {
  if (!Array.isArray(items) || items.length === 0) {
    throw new Error("navRegistry: items must be a non-empty array");
  }
  if (!Array.isArray(groups) || groups.length === 0) {
    throw new Error("navRegistry: groups must be a non-empty array");
  }

  const groupIds = new Set<string>();
  for (const g of groups) {
    if (!g.id || !g.label) {
      throw new Error(`navRegistry: group missing id/label: ${JSON.stringify(g)}`);
    }
    if (groupIds.has(g.id)) {
      throw new Error(`navRegistry: duplicate group id "${g.id}"`);
    }
    groupIds.add(g.id);
  }

  const seenIds = new Set<string>();
  const seenTabIds = new Set<string>();
  const seenIcons = new Set<NavIcon>();
  for (const it of items) {
    if (!it.id) throw new Error("navRegistry: item missing id");
    if (!it.label) throw new Error(`navRegistry: item "${it.id}" missing label`);
    if (!it.tooltip || it.tooltip.trim().length === 0) {
      throw new Error(`navRegistry: item "${it.id}" has empty tooltip`);
    }
    if (!it.tabId) throw new Error(`navRegistry: item "${it.id}" missing tabId`);
    if (typeof it.Icon !== "function") {
      throw new Error(`navRegistry: item "${it.id}" Icon is not renderable`);
    }
    if (typeof it.frequent !== "boolean") {
      throw new Error(`navRegistry: item "${it.id}" frequent must be boolean`);
    }
    if (typeof it.mobilePrimary !== "boolean") {
      throw new Error(`navRegistry: item "${it.id}" mobilePrimary must be boolean`);
    }
    if (typeof it.mobileDrawer !== "boolean") {
      throw new Error(`navRegistry: item "${it.id}" mobileDrawer must be boolean`);
    }
    if (it.mobilePrimary && it.mobileDrawer) {
      throw new Error(
        `navRegistry: item "${it.id}" cannot be both mobilePrimary and mobileDrawer`,
      );
    }
    if (!groupIds.has(it.group)) {
      throw new Error(
        `navRegistry: item "${it.id}" references unknown group "${it.group}"`,
      );
    }
    if (seenIds.has(it.id)) {
      throw new Error(`navRegistry: duplicate item id "${it.id}"`);
    }
    if (seenTabIds.has(it.tabId)) {
      throw new Error(`navRegistry: duplicate tabId "${it.tabId}"`);
    }
    if (seenIcons.has(it.Icon)) {
      throw new Error(`navRegistry: duplicate icon used by item "${it.id}"`);
    }
    seenIds.add(it.id);
    seenTabIds.add(it.tabId);
    seenIcons.add(it.Icon);
  }

  for (const g of groups) {
    if (!items.some((it) => it.group === g.id)) {
      throw new Error(`navRegistry: group "${g.id}" has no items`);
    }
  }

  const freqCount = items.filter((it) => it.frequent).length;
  if (freqCount === 0) {
    throw new Error("navRegistry: at least one item must be frequent");
  }
  if (freqCount === items.length) {
    throw new Error("navRegistry: not all items may be frequent");
  }

  const mobilePrimaryCount = items.filter((it) => it.mobilePrimary).length;
  if (mobilePrimaryCount === 0) {
    throw new Error("navRegistry: at least one item must be mobilePrimary");
  }
  if (mobilePrimaryCount === items.length) {
    throw new Error("navRegistry: not all items may be mobilePrimary");
  }
}

// Fail fast at module load: a malformed registry is a programming error.
assertValidNavRegistry(NAV_ITEMS, NAV_GROUPS);

// ── Selectors (Law of Demeter helpers for view code) ──────────────────────

/** Frequent items, in registry order. */
export function frequentItems(): NavItem[] {
  return NAV_ITEMS.filter((it) => it.frequent);
}

/**
 * Items bucketed by group, in declared group order. Keys are present for
 * every declared group (each is guaranteed non-empty by the contract).
 */
export function itemsByGroup(): Record<NavGroupId, NavItem[]> {
  const out = {} as Record<NavGroupId, NavItem[]>;
  for (const g of NAV_GROUPS) {
    out[g.id] = NAV_ITEMS.filter((it) => it.group === g.id);
  }
  return out;
}

/** Look up a single item by id. */
export function navItemById(id: string): NavItem | undefined {
  return NAV_ITEMS.find((it) => it.id === id);
}

/**
 * Mobile bottom-bar primary items, in registry order — feeds the mobile shell's
 * tablist (Staff, Work, Fleet). The shell appends its own "More" trigger after these.
 */
export function mobilePrimaryItems(): NavItem[] {
  return NAV_ITEMS.filter((it) => it.mobilePrimary);
}

/**
 * Mobile "More" drawer items, in registry order.
 */
export function mobileDrawerItems(): NavItem[] {
  return NAV_ITEMS.filter((it) => it.mobileDrawer);
}
