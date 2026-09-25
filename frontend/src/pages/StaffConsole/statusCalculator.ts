/**
 * statusCalculator.ts — Operational tier and status computation (SC-D3, Issue #1317).
 */

import type { OperationalTier, RoleStatus } from "./rosterTypes";

const TIER_MAPPINGS: Record<string, OperationalTier> = {
  barb: "Leadership",
  board: "Leadership",
  orchestrator: "Leadership",
  "project-steward": "Project Managers",
  librarian: "Specialists",
  cartographer: "Specialists",
  "fleet-critic": "Specialists",
  "research-scout": "Specialists",
  "oss-scout": "Specialists",
  "pragmatic-programmer": "Specialists",
  "fleet-maintenance": "Operations",
  "night-watch": "Operations",
  "issue-remediator": "Operations",
  "pr-remediator": "Operations",
  sanitation: "Operations",
  "usage-tracker": "Operations",
};

/**
 * Normalizes any group string or role name to one of the 4 canonical operational tiers.
 */
export function resolveOperationalTier(roleName: string, rawGroup?: string | null): OperationalTier {
  if (rawGroup) {
    const normalized = rawGroup.trim().toLowerCase();
    if (normalized.includes("lead")) return "Leadership";
    if (normalized.includes("project") || normalized.includes("manager") || normalized.includes("pm"))
      return "Project Managers";
    if (normalized.includes("special")) return "Specialists";
    if (normalized.includes("operat") || normalized.includes("ops")) return "Operations";
  }
  return TIER_MAPPINGS[roleName] || "Specialists";
}

/**
 * Calculates the role's 5-state lifecycle status and a descriptive reason.
 */
export function calculateRoleStatus(
  role: {
    valid?: boolean;
    errors?: string[];
    error?: string | null;
    retired?: boolean;
    dispatchable?: boolean;
    providers?: string[];
    holds?: string[];
    active_runs?: number;
    unread_count?: number;
  },
  installedProviders?: Record<string, boolean>
): { status: RoleStatus; status_reason: string } {
  // 1. Invalid: Schema validation failure
  if (role.valid === false || (role.errors && role.errors.length > 0) || role.error) {
    const err = role.error || (role.errors ? role.errors.join("; ") : "Invalid role file");
    return {
      status: "invalid",
      status_reason: `Invalid role file: ${err}`,
    };
  }

  // 2. Unavailable: Holds active, missing provider, or retired
  if (role.retired) {
    return {
      status: "unavailable",
      status_reason: "Role is retired",
    };
  }
  if (role.holds && role.holds.length > 0) {
    return {
      status: "unavailable",
      status_reason: `Role on operational hold (${role.holds.join(", ")})`,
    };
  }
  if (role.dispatchable === false) {
    const missing = role.providers && role.providers.length > 0 ? ` (${role.providers.join(", ")})` : "";
    return {
      status: "unavailable",
      status_reason: `No provider signed in / installed${missing}`,
    };
  }
  if (installedProviders && role.providers && role.providers.length > 0) {
    const anyInstalled = role.providers.some((p) => installedProviders[p]);
    if (!anyInstalled) {
      return {
        status: "unavailable",
        status_reason: `No provider signed in / installed (${role.providers.join(", ")})`,
      };
    }
  }

  // 3. Needs you: Unread messages or attention needed
  if ((role.unread_count ?? 0) > 0) {
    const count = role.unread_count ?? 0;
    return {
      status: "needs you",
      status_reason: `Needs your attention (${count} unread ${count === 1 ? "message" : "messages"})`,
    };
  }

  // 4. Working: Active runs in progress
  if ((role.active_runs ?? 0) > 0) {
    const count = role.active_runs ?? 0;
    return {
      status: "working",
      status_reason: `Working on ${count} active ${count === 1 ? "run" : "runs"}`,
    };
  }

  // 5. Idle: Ready for assignment
  return {
    status: "idle",
    status_reason: "Idle · Ready for assignment",
  };
}

/**
 * Returns a human-friendly relative age (e.g. "now", "5m ago", "2h ago", "yesterday").
 */
export function formatRelativeAge(isoString: string): string {
  try {
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return "";
    const now = Date.now();
    const diffSec = Math.floor((now - date.getTime()) / 1000);

    if (diffSec < 60) return "just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}h ago`;
    const diffDays = Math.floor(diffHour / 24);
    if (diffDays === 1) return "yesterday";
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  } catch {
    return "";
  }
}
