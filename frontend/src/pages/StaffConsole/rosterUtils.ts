/**
 * rosterUtils.ts — Categorization, status evaluation, search filtering,
 * and formatting utilities for Staff Console Roster.
 *
 * Implements SC-D3 (Issue #1317) under Epic SC-D (#1350).
 */
import type { RosterGroupKey, RosterStatus, StaffRoleItem } from "./types";

/**
 * Evaluates the live operational status of a staff role.
 *
 * Priority order:
 * 1. invalid: role definition error or valid: false
 * 2. unavailable: operational block (held, budget reached, or no provider signed in)
 * 3. needs_you: pending human action proposal or unread message count
 * 4. working: role is actively executing runs
 * 5. idle: available and ready for dispatch
 */
export function computeRoleStatus(
  role: StaffRoleItem,
  availableProviders?: Record<string, boolean>
): { status: RosterStatus; reason?: string } {
  // 1. Invalid role specification
  if (role.valid === false || (role.errors && role.errors.length > 0) || Boolean(role.error)) {
    const errorDetails =
      role.error || (role.errors && role.errors.length > 0 ? role.errors.join("; ") : null);
    return {
      status: "invalid",
      reason: errorDetails || "invalid role file",
    };
  }

  // 2. Unavailable operational blocks
  // 2a. Operational holds active
  if (role.holds && role.holds.length > 0) {
    return {
      status: "unavailable",
      reason: `held: ${role.holds.join(", ")}`,
    };
  }

  // 2b. Budget limit reached
  if (
    role.budget &&
    typeof role.budget.daily_limit === "number" &&
    typeof role.budget.spend_today === "number" &&
    role.budget.daily_limit > 0 &&
    role.budget.spend_today >= role.budget.daily_limit
  ) {
    return {
      status: "unavailable",
      reason: "budget reached",
    };
  }

  // 2c. No provider signed in
  if (
    role.providers &&
    role.providers.length > 0 &&
    availableProviders &&
    role.providers.every((p) => availableProviders[p] === false)
  ) {
    return {
      status: "unavailable",
      reason: "no provider signed in",
    };
  }

  // 3. Needs human attention / review
  if (
    (typeof role.pending_proposals_count === "number" && role.pending_proposals_count > 0) ||
    (typeof role.caller_unread_count === "number" && role.caller_unread_count > 0)
  ) {
    return {
      status: "needs_you",
      reason: "needs you",
    };
  }

  // 4. Actively working
  if (typeof role.active_runs === "number" && role.active_runs > 0) {
    return {
      status: "working",
      reason: "working",
    };
  }

  // 5. Default idle
  return {
    status: "idle",
    reason: "idle",
  };
}

/**
 * Normalizes group names into the 4 standard operational tiers from SC-D1.
 */
export function categorizeRole(role: StaffRoleItem): RosterGroupKey {
  if (role.group) {
    const norm = role.group.trim().toLowerCase().replace(/[-_]/g, " ");
    if (norm.includes("lead")) return "leadership";
    if (norm.includes("project") || norm.includes("steward") || norm.includes("manager")) {
      return "project_managers";
    }
    if (norm.includes("special")) return "specialists";
    if (norm.includes("operat") || norm.includes("maint")) return "operations";
  }

  const name = role.name.toLowerCase();
  if (name === "barb" || name === "board" || name === "orchestrator") {
    return "leadership";
  }
  if (name.includes("steward") || name.includes("project")) {
    return "project_managers";
  }
  if (
    name.includes("maintenance") ||
    name.includes("night-watch") ||
    name.includes("night_watch") ||
    name.includes("remediator") ||
    name.includes("sanitation") ||
    name.includes("usage")
  ) {
    return "operations";
  }

  return "specialists";
}

/**
 * Filters roles matching search query against name, title, or mandate summary.
 */
export function filterRoles(roles: StaffRoleItem[], query: string): StaffRoleItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return roles;

  return roles.filter((role) => {
    const nameMatch = role.name.toLowerCase().includes(q);
    const titleMatch = role.title.toLowerCase().includes(q);
    const summaryMatch = Boolean(role.summary && role.summary.toLowerCase().includes(q));
    return nameMatch || titleMatch || summaryMatch;
  });
}

/**
 * Formats ISO timestamp or date into human-friendly relative age string.
 */
export function formatRelativeTime(isoString?: string | null): string {
  if (!isoString) return "";
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "";

  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return "just now";

  const diffSecs = Math.floor(diffMs / 1000);
  if (diffSecs < 60) return "just now";

  const diffMins = Math.floor(diffSecs / 60);
  if (diffMins < 60) return `${diffMins}m ago`;

  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}
