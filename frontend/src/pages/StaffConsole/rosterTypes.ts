/**
 * rosterTypes.ts — Type definitions for Staff Console Roster (SC-D3, Issue #1317).
 */

export type RoleStatus = "idle" | "working" | "needs you" | "unavailable" | "invalid";

export type OperationalTier =
  | "Leadership"
  | "Project Managers"
  | "Specialists"
  | "Operations";

export const OPERATIONAL_TIERS: readonly OperationalTier[] = [
  "Leadership",
  "Project Managers",
  "Specialists",
  "Operations",
] as const;

export interface LastMessagePreview {
  body_md: string;
  author: string;
  created_at: string;
}

export interface RosterRole {
  name: string;
  title: string;
  summary: string;
  group?: string | null;
  providers: string[];
  dispatchable: boolean;
  valid: boolean;
  errors?: string[];
  error?: string | null;
  active_runs: number;
  unread_count: number;
  status: RoleStatus;
  status_reason: string;
  last_message?: LastMessagePreview | null;
  budget?: {
    usd_per_run?: number | null;
    usd_per_day?: number | null;
  };
  holds?: string[];
  retired?: boolean;
}

export interface RosterProps {
  roles: RosterRole[];
  selectedRole: string | null;
  onSelectRole: (roleName: string) => void;
  pinnedRoles: string[];
  onTogglePin: (roleName: string) => void;
  isStale?: boolean;
  staleReason?: string | null;
  onRetry?: () => void;
  isLoading?: boolean;
  className?: string;
}

export interface RosterRowProps {
  role: RosterRole;
  isSelected: boolean;
  isPinned: boolean;
  isFocused?: boolean;
  onSelect: () => void;
  onTogglePin: () => void;
  testIdSuffix?: string;
}

export interface RosterGroupProps {
  tier: OperationalTier;
  roles: RosterRole[];
  selectedRole: string | null;
  focusedRole: string | null;
  pinnedRoles: string[];
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onSelectRole: (name: string) => void;
  onTogglePin: (name: string) => void;
}
