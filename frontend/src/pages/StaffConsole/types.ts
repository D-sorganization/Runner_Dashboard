/**
 * types.ts — Types and interfaces for the Staff Console Roster.
 *
 * Implements SC-D3 (Issue #1317) under Epic SC-D (#1350) / Umbrella #1354.
 */

export type RosterStatus =
  | "idle"
  | "working"
  | "needs_you"
  | "unavailable"
  | "invalid"
  | "retired";

export type RosterGroupKey =
  | "pinned"
  | "leadership"
  | "advisors"
  | "project_managers"
  | "specialists"
  | "operations";

export interface RosterGroupMeta {
  key: RosterGroupKey;
  label: string;
  description: string;
}

export const ROSTER_GROUPS: readonly RosterGroupMeta[] = [
  {
    key: "leadership",
    label: "Leadership",
    description: "Executive strategy, attention gate and governance",
  },
  {
    key: "advisors",
    label: "Advisors",
    description: "Knowledge keeping, compiled findings, and frontier direction",
  },
  {
    key: "project_managers",
    label: "Project Managers",
    description: "Cross-repo planning, backlog tracking, and steward governance",
  },
  {
    key: "specialists",
    label: "Specialists",
    description: "Domain-specific architectural and code intelligence",
  },
  {
    key: "operations",
    label: "Operations",
    description: "Fleet hygiene, remediation, night watch, and maintenance",
  },
] as const;

export interface StaffRoleBudget {
  usd_per_run?: number;
  usd_per_day?: number;
  max_minutes?: number;
  idle_minutes?: number;
  /** Not yet served by GET /api/v1/staff/roster; the API sends no per-role spend (#1744). */
  spend_today?: number;
  currency?: string;
  [key: string]: unknown;
}

export interface StaffRoleItem {
  name: string;
  title: string;
  summary?: string;
  group?: string | null;
  avatar?: string;
  valid?: boolean;
  error?: string | null;
  errors?: string[];
  dispatchable?: boolean;
  active_runs?: number;
  providers?: string[];
  budget?: StaffRoleBudget;
  holds?: string[];
  caller_unread_count?: number;
  pending_proposals_count?: number;
  last_message_preview?: string;
  last_message_at?: string;
  schedule?: string | null;
  window?: { start: string; end: string } | string | null;
  retired?: boolean;
  retired_reason?: string;
  [key: string]: unknown;
}

export interface RosterProps {
  roles?: StaffRoleItem[];
  selectedRoleId?: string;
  onSelectRole?: (roleId: string) => void;
  pinnedRoleIds?: string[];
  onTogglePin?: (roleId: string) => void;
  availableProviders?: Record<string, boolean>;
  isLoading?: boolean;
  isError?: boolean;
  errorMessage?: string;
  isStale?: boolean;
  className?: string;
  onThreadCreated?: (thread: import("./threadTypes").ThreadInfo) => void;
}

export interface RosterRowProps {
  role: StaffRoleItem;
  isSelected: boolean;
  isPinned: boolean;
  onSelect: (roleId: string) => void;
  onTogglePin?: (roleId: string) => void;
  availableProviders?: Record<string, boolean>;
  isFocused?: boolean;
  isAutoRoute?: boolean;
}

export interface RosterGroupProps {
  groupKey: RosterGroupKey;
  label: string;
  roles: StaffRoleItem[];
  isCollapsed: boolean;
  onToggleCollapse: (groupKey: RosterGroupKey) => void;
  selectedRoleId?: string;
  pinnedRoleIds: string[];
  onSelectRole: (roleId: string) => void;
  onTogglePin?: (roleId: string) => void;
  availableProviders?: Record<string, boolean>;
  focusedRoleId?: string;
}
