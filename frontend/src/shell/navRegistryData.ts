/**
 * navRegistryData.ts — Nav items and groups definition (SC-D2 / issue #1309).
 *
 * Defines the 4-area information architecture:
 *   - Staff (Staff Console, Fleet Command, Maxwell)
 *   - Work (Queue, Remediation, Workflows, Agent Dispatch, Code Requests, Scheduled, Projects)
 *   - Fleet (Fleet Overview, Machines, Runner Plan, Runner Audit, Event Log, Conductor, Orchestration, Deployment, Insights, Assessments, Org)
 *   - Settings (Settings, Credentials, Notifications, Linear Setup, Local Tools, Tests, Diagnostics, Principals)
 */
import type { NavIcon } from "./navIcons";
import {
  ServerIcon,
  QueueIcon,
  WrenchIcon,
  RepoIcon,
  FlaskIcon,
  ActivityIcon,
  KeyIcon,
  BotIcon,
  ChartIcon,
  InboxIcon,
  SettingsIcon,
  UsersIcon,
  NetworkIcon,
  FlagIcon,
  LinearIcon,
  BellIcon,
  ClipboardCheckIcon,
  HardDriveIcon,
  BriefcaseIcon,
  CompassIcon,
} from "./navIcons";

export type NavGroupId = "staff" | "work" | "fleet" | "settings";

export interface NavGroup {
  id: NavGroupId;
  label: string;
}

export interface NavItem {
  id: string;
  label: string;
  mobileLabel?: string;
  group: NavGroupId;
  Icon: NavIcon;
  tooltip: string;
  tabId: string;
  frequent: boolean;
  mobilePrimary: boolean;
  mobileDrawer: boolean;
}

export const NAV_GROUPS: readonly NavGroup[] = [
  { id: "staff", label: "Staff" },
  { id: "work", label: "Work" },
  { id: "fleet", label: "Fleet" },
  { id: "settings", label: "Settings" },
] as const;

export const NAV_ITEMS: readonly NavItem[] = [
  // ── Staff ────────────────────────────────────────────────────────────────
  {
    id: "staff",
    label: "Staff Console",
    mobileLabel: "Staff",
    group: "staff",
    Icon: BriefcaseIcon,
    tooltip: "Staff Hub & Console: talk to AI staff, view roster, live tails and holds.",
    tabId: "staff",
    frequent: true,
    mobilePrimary: true,
    mobileDrawer: false,
  },
  {
    id: "fleet-command",
    label: "Fleet Command",
    group: "staff",
    Icon: CompassIcon,
    tooltip: "Fleet Command: board priorities, directives, who is working on what, claims and staff dispatch.",
    tabId: "fleet-command",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "maxwell",
    label: "Maxwell",
    group: "staff",
    Icon: BotIcon,
    tooltip: "Maxwell autonomous AI control plane: status and tasks.",
    tabId: "maxwell",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },

  // ── Work ─────────────────────────────────────────────────────────────────
  {
    id: "queue",
    label: "Queue",
    mobileLabel: "Work",
    group: "work",
    Icon: QueueIcon,
    tooltip: "Queued and in-progress workflow runs across the fleet.",
    tabId: "queue",
    frequent: true,
    mobilePrimary: true,
    mobileDrawer: false,
  },
  {
    id: "remediation",
    label: "Remediation",
    group: "work",
    Icon: WrenchIcon,
    tooltip: "Automated and operator-driven run remediation.",
    tabId: "remediation",
    frequent: true,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "workflows",
    label: "Workflows",
    group: "work",
    Icon: ActivityIcon,
    tooltip: "Workflow inventory across the organization.",
    tabId: "workflows",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "code-requests",
    label: "Code Requests",
    group: "work",
    Icon: InboxIcon,
    tooltip: "Code requests, feature proposals and their lifecycle states.",
    tabId: "code-requests",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "projects",
    label: "Projects",
    group: "work",
    Icon: FlagIcon,
    tooltip: "Per-repo project charter progress, decisions needed and the last project-steward run.",
    tabId: "projects",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },

  // ── Fleet ────────────────────────────────────────────────────────────────
  {
    id: "overview",
    label: "Overview",
    mobileLabel: "Fleet",
    group: "fleet",
    Icon: ServerIcon,
    tooltip: "Live fleet overview: runner health, status, and key metrics.",
    tabId: "overview",
    frequent: true,
    mobilePrimary: true,
    mobileDrawer: false,
  },
  {
    id: "operations",
    label: "Operations",
    mobileLabel: "Ops",
    group: "fleet",
    Icon: NetworkIcon,
    tooltip: "Fleet operations: deployment rollouts, Conductor admission, runner hours, scheduled workflows, and diagnostics.",
    tabId: "operations",
    frequent: true,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "insights",
    label: "Insights",
    group: "fleet",
    Icon: ChartIcon,
    tooltip: "Fleet reports, run analysis, and historical trends.",
    tabId: "insights",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "assessments",
    label: "Assessments",
    group: "fleet",
    Icon: ClipboardCheckIcon,
    tooltip: "Repository health assessments and graded scores.",
    tabId: "assessments",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "org",
    label: "Organization",
    group: "fleet",
    Icon: RepoIcon,
    tooltip: "Organization-wide repository and runner overview.",
    tabId: "org",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },

  // ── Settings ─────────────────────────────────────────────────────────────
  {
    id: "settings",
    label: "Preferences",
    group: "settings",
    Icon: SettingsIcon,
    tooltip: "Dashboard settings and preferences.",
    tabId: "settings",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: true,
  },
  {
    id: "credentials",
    label: "Credentials",
    group: "settings",
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
    group: "settings",
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
    group: "settings",
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
    group: "settings",
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
    group: "settings",
    Icon: FlaskIcon,
    tooltip: "Test suites and their latest results.",
    tabId: "tests",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: false,
  },
  {
    id: "principals",
    label: "Principals",
    group: "settings",
    Icon: UsersIcon,
    tooltip: "Authenticated principals and acting-as identities.",
    tabId: "principals",
    frequent: false,
    mobilePrimary: false,
    mobileDrawer: false,
  },
] as const;
