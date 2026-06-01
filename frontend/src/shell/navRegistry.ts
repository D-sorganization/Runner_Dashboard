/**
 * navRegistry.ts — the single typed source of truth for every navigable
 * category in the dashboard (issue #797, part of epic #796).
 *
 * Every shell surface renders from this one registry (DRY):
 *   - the GitHub-style left sidebar (#798), grouped by `group`;
 *   - the slim top toolstrip (#799), filtered to `frequent === true`;
 *   - the grouped dropdown menus (#800).
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
  ServerIcon,
  QueueIcon,
  WrenchIcon,
  ConductorIcon,
  RepoIcon,
  FlaskIcon,
  ActivityIcon,
  ClockIcon,
  RocketIcon,
  CalendarIcon,
  TerminalIcon,
  KeyIcon,
  BotIcon,
  ChartIcon,
  InboxIcon,
  ShieldIcon,
  SettingsIcon,
  UsersIcon,
  StethoscopeIcon,
} from "./navIcons";

/** Ordered group identifiers used to bucket categories in the sidebar. */
export type NavGroupId =
  | "fleet"
  | "workflows"
  | "orchestration"
  | "agents"
  | "analysis"
  | "admin";

export interface NavGroup {
  /** Stable group identifier (referenced by NavItem.group). */
  id: NavGroupId;
  /** Human-readable section heading shown in the sidebar. */
  label: string;
}

export interface NavItem {
  /** Stable, unique identifier for this nav item. */
  id: string;
  /** Short label shown in nav surfaces. */
  label: string;
  /** Group this item belongs to (must match a declared NavGroup.id). */
  group: NavGroupId;
  /** Renderable icon component. */
  Icon: NavIcon;
  /** One-line description shown as an accessible hover/focus tooltip. */
  tooltip: string;
  /** Legacy App tab string this item activates when selected. */
  tabId: string;
  /** True if the item belongs in the slim top toolstrip (most-frequent). */
  frequent: boolean;
}

/** Declared groups, in the order they appear in the sidebar. */
export const NAV_GROUPS: readonly NavGroup[] = [
  { id: "fleet", label: "Fleet & Runners" },
  { id: "workflows", label: "Workflows & Jobs" },
  { id: "orchestration", label: "Orchestration" },
  { id: "agents", label: "AI & Agents" },
  { id: "analysis", label: "Reports & Analysis" },
  { id: "admin", label: "Admin & Settings" },
] as const;

/**
 * The category registry. Order within a group is the display order.
 * `frequent: true` items also surface in the slim top toolstrip.
 */
export const NAV_ITEMS: readonly NavItem[] = [
  // ── Fleet & Runners ──────────────────────────────────────────────────────
  {
    id: "overview",
    label: "Fleet",
    group: "fleet",
    Icon: ServerIcon,
    tooltip: "Live fleet overview: runner health, status, and key metrics.",
    tabId: "overview",
    frequent: true,
  },
  {
    id: "queue",
    label: "Queue",
    group: "fleet",
    Icon: QueueIcon,
    tooltip: "Queued and in-progress workflow runs across the fleet.",
    tabId: "queue",
    frequent: true,
  },
  {
    id: "machines",
    label: "Machines",
    group: "fleet",
    Icon: ServerIcon,
    tooltip: "Heavy / multi-node runner machines and their capacity.",
    tabId: "machines",
    frequent: false,
  },
  {
    id: "runner-schedule",
    label: "Runner Plan",
    group: "fleet",
    Icon: ClockIcon,
    tooltip: "Scheduled runner on/off plan and desired-capacity state.",
    tabId: "runner-schedule",
    frequent: false,
  },
  {
    id: "runner-audit",
    label: "Runner Audit",
    group: "fleet",
    Icon: ShieldIcon,
    tooltip: "Hosted-runner billing audit and routing-policy violations.",
    tabId: "runner-audit",
    frequent: false,
  },

  // ── Workflows & Jobs ─────────────────────────────────────────────────────
  {
    id: "remediation",
    label: "Remediation",
    group: "workflows",
    Icon: WrenchIcon,
    tooltip: "Failed runs and AI-assisted remediation actions.",
    tabId: "remediation",
    frequent: true,
  },
  {
    id: "workflows",
    label: "Workflows",
    group: "workflows",
    Icon: ActivityIcon,
    tooltip: "Workflow inventory across the organization.",
    tabId: "workflows",
    frequent: false,
  },
  {
    id: "scheduled-jobs",
    label: "Schedules",
    group: "workflows",
    Icon: CalendarIcon,
    tooltip: "Scheduled (cron) workflows and their next-run times.",
    tabId: "scheduled-jobs",
    frequent: false,
  },

  // ── Orchestration ────────────────────────────────────────────────────────
  {
    id: "conductor",
    label: "Conductor",
    group: "orchestration",
    Icon: ConductorIcon,
    tooltip: "Conductor admission gate: dispatch visibility and control.",
    tabId: "conductor",
    frequent: true,
  },
  {
    id: "agent-dispatch",
    label: "Dispatch",
    group: "orchestration",
    Icon: RocketIcon,
    tooltip: "Dispatch AI agents to remediate failures or run tasks.",
    tabId: "agent-dispatch",
    frequent: false,
  },
  {
    id: "fleet-orchestration",
    label: "Fleet Orchestration",
    group: "orchestration",
    Icon: ConductorIcon,
    tooltip: "Cross-node fleet orchestration and coordination state.",
    tabId: "fleet-orchestration",
    frequent: false,
  },
  {
    id: "deployment",
    label: "Deployment",
    group: "orchestration",
    Icon: RocketIcon,
    tooltip: "Deployment rollout state and version drift across machines.",
    tabId: "deployment",
    frequent: false,
  },

  // ── AI & Agents ──────────────────────────────────────────────────────────
  {
    id: "maxwell",
    label: "Maxwell",
    group: "agents",
    Icon: BotIcon,
    tooltip: "Maxwell autonomous AI control plane: status and tasks.",
    tabId: "maxwell",
    frequent: false,
  },
  {
    id: "cline-launcher",
    label: "Cline Launcher",
    group: "agents",
    Icon: TerminalIcon,
    tooltip: "Launch Cline agent sessions against the fleet.",
    tabId: "cline-launcher",
    frequent: false,
  },

  // ── Reports & Analysis ───────────────────────────────────────────────────
  {
    id: "analysis",
    label: "Analysis",
    group: "analysis",
    Icon: ChartIcon,
    tooltip: "Enriched run analysis and historical reports.",
    tabId: "analysis",
    frequent: false,
  },
  {
    id: "assessments",
    label: "Assessments",
    group: "analysis",
    Icon: ActivityIcon,
    tooltip: "Repository health assessments and graded scores.",
    tabId: "assessments",
    frequent: false,
  },
  {
    id: "feature-requests",
    label: "Feature Requests",
    group: "analysis",
    Icon: InboxIcon,
    tooltip: "Incoming feature requests and their triage status.",
    tabId: "feature-requests",
    frequent: false,
  },
  {
    id: "org",
    label: "Organization",
    group: "analysis",
    Icon: RepoIcon,
    tooltip: "Organization-wide repository and runner overview.",
    tabId: "org",
    frequent: false,
  },

  // ── Admin & Settings ─────────────────────────────────────────────────────
  {
    id: "credentials",
    label: "Credentials",
    group: "admin",
    Icon: KeyIcon,
    tooltip: "Stored credentials and their readiness state.",
    tabId: "credentials",
    frequent: false,
  },
  {
    id: "local-apps",
    label: "Local Tools",
    group: "admin",
    Icon: TerminalIcon,
    tooltip: "Local application processes and their health.",
    tabId: "local-apps",
    frequent: false,
  },
  {
    id: "tests",
    label: "Tests",
    group: "admin",
    Icon: FlaskIcon,
    tooltip: "Test suites and their latest results.",
    tabId: "tests",
    frequent: false,
  },
  {
    id: "diagnostics",
    label: "Diagnostics",
    group: "admin",
    Icon: StethoscopeIcon,
    tooltip: "Dashboard diagnostics and self-checks.",
    tabId: "diagnostics",
    frequent: false,
  },
  {
    id: "principals",
    label: "Principals",
    group: "admin",
    Icon: UsersIcon,
    tooltip: "Authenticated principals and acting-as identities.",
    tabId: "principals",
    frequent: false,
  },
  {
    id: "settings",
    label: "Settings",
    group: "admin",
    Icon: SettingsIcon,
    tooltip: "Dashboard settings and preferences.",
    tabId: "settings",
    frequent: false,
  },
] as const;

// ── Design-by-Contract validation ─────────────────────────────────────────

/**
 * Validate the registry's structural invariants. Throws on any violation.
 *
 * Preconditions enforced:
 *  - every item has non-empty id, label, tooltip, and tabId;
 *  - ids and tabIds are unique;
 *  - group ids are unique;
 *  - every item.group references a declared group;
 *  - every declared group has at least one item;
 *  - at least one (but not all) items are `frequent`.
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
    seenIds.add(it.id);
    seenTabIds.add(it.tabId);
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
}

// Fail fast at module load: a malformed registry is a programming error.
assertValidNavRegistry(NAV_ITEMS, NAV_GROUPS);

// ── Selectors (Law of Demeter helpers for view code) ──────────────────────

/** Frequent items, in registry order — feeds the slim top toolstrip. */
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
