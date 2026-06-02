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
  CpuIcon,
  NetworkIcon,
  PackageIcon,
  FileTextIcon,
  LinearIcon,
  BellIcon,
  ClipboardCheckIcon,
  HardDriveIcon,
  ScrollTextIcon,
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
  /**
   * True if the item is one of the bottom-bar primary tabs on the mobile shell
   * (issue #821). Exactly the small set an on-call operator reaches for first.
   * The mobile shell reserves a final slot for the "More" drawer trigger, so the
   * number of `mobilePrimary` items must stay small (see the contract below).
   */
  mobilePrimary: boolean;
  /**
   * True if the item is surfaced in the mobile "More" drawer (issue #821).
   * Primary and drawer are mutually exclusive: a primary tab is already on the
   * bottom bar, so it must not also appear in the drawer. Items that are neither
   * are intentionally desktop-only.
   */
  mobileDrawer: boolean;
}

/** Declared groups, in the order they appear in the sidebar. */
export const NAV_GROUPS: readonly NavGroup[] = [
  { id: "fleet", label: "Fleet & Runners" },
  { id: "workflows", label: "Workflows & Jobs" },
  { id: "orchestration", label: "Orchestration" },
  { id: "agents", label: "AI & Agents" },
  { id: "analysis", label: "Reports & Insights" },
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
    mobilePrimary: true,
    mobileDrawer: false,
  },
  {
    id: "queue",
    label: "Queue",
    group: "fleet",
    Icon: QueueIcon,
    tooltip: "Queued and in-progress workflow runs across the fleet.",
    tabId: "queue",
    frequent: true,
    mobilePrimary: true,
    mobileDrawer: false,
  },
  {
    id: "machines",
    label: "Machines",
    group: "fleet",
    Icon: CpuIcon,
    tooltip: "Heavy / multi-node runner machines and their capacity.",
    tabId: "machines",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "runner-schedule",
    label: "Runner Plan",
    group: "fleet",
    Icon: ClockIcon,
    tooltip: "Scheduled runner on/off plan and desired-capacity state.",
    tabId: "runner-schedule",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "runner-audit",
    label: "Runner Audit",
    group: "fleet",
    Icon: ShieldIcon,
    tooltip: "Hosted-runner billing audit and routing-policy violations.",
    tabId: "runner-audit",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "events",
    label: "Event Log",
    group: "fleet",
    Icon: ScrollTextIcon,
    tooltip:
      "Durable fleet event history + alarm center: runner offline/online, disk pressure, saturation, watchdog.",
    tabId: "events",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
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
    mobilePrimary: true,
    mobileDrawer: false,
  },
  {
    id: "workflows",
    label: "Workflows",
    group: "workflows",
    Icon: ActivityIcon,
    tooltip: "Workflow inventory across the organization.",
    tabId: "workflows",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "scheduled-jobs",
    label: "Schedules",
    group: "workflows",
    Icon: CalendarIcon,
    tooltip: "Scheduled (cron) workflows and their next-run times.",
    tabId: "scheduled-jobs",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },

  // ── Orchestration ────────────────────────────────────────────────────────
  {
    id: "conductor",
    label: "Conductor",
    group: "orchestration",
    Icon: ConductorIcon,
    tooltip: "Conductor admission gate: pause, drain and budget controls.",
    tabId: "conductor",
    frequent: true,
    // The on-call incident controls (pause/drain/budget) an operator needs on a
    // phone — surfaced as a first-class mobile drawer entry (issue #821).
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "agent-dispatch",
    label: "Dispatch",
    group: "orchestration",
    Icon: RocketIcon,
    tooltip: "Dispatch AI agents to remediate failures or run tasks.",
    tabId: "agent-dispatch",
    frequent: false,
    // AgentDispatch as a first-class mobile drawer tab (issue #821).
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "fleet-orchestration",
    label: "Fleet Orchestration",
    group: "orchestration",
    Icon: NetworkIcon,
    tooltip: "Cross-node fleet orchestration and coordination state.",
    tabId: "fleet-orchestration",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "deployment",
    label: "Deployment",
    group: "orchestration",
    Icon: PackageIcon,
    tooltip: "Deployment rollout state and version drift across machines.",
    tabId: "deployment",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
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
    mobilePrimary: true,
    mobileDrawer: false,
  },
  {
    id: "cline-launcher",
    label: "Cline Launcher",
    group: "agents",
    Icon: TerminalIcon,
    tooltip: "Launch Cline agent sessions against the fleet.",
    tabId: "cline-launcher",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },

  // ── Reports & Insights ───────────────────────────────────────────────────
  {
    id: "reports",
    label: "Reports",
    group: "analysis",
    Icon: FileTextIcon,
    tooltip: "Saved report files: open, browse, and download fleet reports.",
    tabId: "reports",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "analysis",
    label: "Analysis",
    group: "analysis",
    Icon: ChartIcon,
    tooltip: "Enriched run analysis and historical trends.",
    tabId: "analysis",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "assessments",
    label: "Assessments",
    group: "analysis",
    Icon: ClipboardCheckIcon,
    tooltip: "Repository health assessments and graded scores.",
    tabId: "assessments",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "feature-requests",
    label: "Feature Requests",
    group: "analysis",
    Icon: InboxIcon,
    tooltip: "Incoming feature requests and their triage status.",
    tabId: "feature-requests",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "org",
    label: "Organization",
    group: "analysis",
    Icon: RepoIcon,
    tooltip: "Organization-wide repository and runner overview.",
    tabId: "org",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
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
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "linear-setup",
    label: "Linear Setup",
    group: "admin",
    Icon: LinearIcon,
    tooltip: "Connect Linear workspaces and configure issue-sync webhooks.",
    tabId: "linear-setup",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: false,
  },
  {
    id: "push-settings",
    label: "Notifications",
    group: "admin",
    Icon: BellIcon,
    tooltip: "Web-push notification settings: subscribe and choose alert topics.",
    tabId: "push-settings",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "local-apps",
    label: "Local Tools",
    group: "admin",
    Icon: HardDriveIcon,
    tooltip: "Local application processes and their health.",
    tabId: "local-apps",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: false,
  },
  {
    id: "tests",
    label: "Tests",
    group: "admin",
    Icon: FlaskIcon,
    tooltip: "Test suites and their latest results.",
    tabId: "tests",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: false,
  },
  {
    id: "diagnostics",
    label: "Diagnostics",
    group: "admin",
    Icon: StethoscopeIcon,
    tooltip: "Dashboard diagnostics and self-checks.",
    tabId: "diagnostics",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: false,
  },
  {
    id: "principals",
    label: "Principals",
    group: "admin",
    Icon: UsersIcon,
    tooltip: "Authenticated principals and acting-as identities.",
    tabId: "principals",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: false,
  },
  {
    id: "settings",
    label: "Settings",
    group: "admin",
    Icon: SettingsIcon,
    tooltip: "Dashboard settings and preferences.",
    tabId: "settings",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
] as const;

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

/**
 * Mobile bottom-bar primary items, in registry order — feeds the mobile shell's
 * tablist (issue #821). The shell appends its own "More" trigger after these.
 */
export function mobilePrimaryItems(): NavItem[] {
  return NAV_ITEMS.filter((it) => it.mobilePrimary);
}

/**
 * Mobile "More" drawer items, in registry order — feeds the mobile drawer
 * (issue #821). Includes the previously desktop-only operator controls
 * (Conductor pause/drain/budget, AgentDispatch, …).
 */
export function mobileDrawerItems(): NavItem[] {
  return NAV_ITEMS.filter((it) => it.mobileDrawer);
}
