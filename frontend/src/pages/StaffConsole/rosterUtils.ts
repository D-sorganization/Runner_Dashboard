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
 * 2. retired: role.retired is true; the reason is its retired_reason (#1759)
 * 3. unavailable: operational block (held, budget reached, or no provider signed in)
 * 4. needs_you: pending human action proposal or unread message count
 * 5. working: role is actively executing runs
 * 6. idle: available and ready for dispatch
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

  // 2. Retired role (#1759). A retired role must never read as available
  // ("Idle"); it reads "Retired" and carries its retired_reason when it has one.
  if (role.retired) {
    return {
      status: "retired",
      reason: role.retired_reason || "retired",
    };
  }

  // 3. Unavailable operational blocks
  // 3a. `role.holds` is a role's declared `holds:` YAML list — always guardrails (owner
  // decision, #1726): they stay in the role prompt as standing rules but never block
  // scheduling, so they do not make the role unavailable. See getRoleTooltipText for
  // where they are still surfaced, informationally, to the operator.

  // 3b. Budget limit reached
  if (
    role.budget &&
    typeof role.budget.usd_per_day === "number" &&
    typeof role.budget.spend_today === "number" &&
    role.budget.usd_per_day > 0 &&
    role.budget.spend_today >= role.budget.usd_per_day
  ) {
    return {
      status: "unavailable",
      reason: "budget reached",
    };
  }

  // 3c. No provider signed in
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

  // 4. Needs human attention / review
  if (
    (typeof role.pending_proposals_count === "number" && role.pending_proposals_count > 0) ||
    (typeof role.caller_unread_count === "number" && role.caller_unread_count > 0)
  ) {
    return {
      status: "needs_you",
      reason: "needs you",
    };
  }

  // 5. Actively working
  if (typeof role.active_runs === "number" && role.active_runs > 0) {
    return {
      status: "working",
      reason: "working",
    };
  }

  // 6. Default idle
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
    if (norm.includes("advisor")) return "advisors";
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
  if (
    name === "disciple" ||
    name === "vision-quest" ||
    name === "vision_quest" ||
    name === "mad-scientist"
  ) {
    return "advisors";
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

/**
 * Returns full tooltip text describing the role's status.
 * For roles with standing rules (guardrail holds, #1726), lists every one in full;
 * they are informational and do not change the role's status.
 */
export function getRoleTooltipText(
  role: StaffRoleItem,
  availableProviders?: Record<string, boolean>
): string {
  if (role.holds && role.holds.length > 0) {
    return `Standing rule: ${role.holds.join(", ")}`;
  }
  const { reason, status } = computeRoleStatus(role, availableProviders);
  return reason || status;
}

/**
 * Formats a role's schedule window for display. `GET /api/v1/staff/roster`
 * sends `window` as null, `{start, end}` (HH:MM), or a plain string; this is
 * the single place that turns it into text so it never renders as
 * `[object Object]` (#1744).
 */
export function formatRoleWindow(
  window: { start: string; end: string } | Record<string, string> | string | null | undefined
): string {
  if (window == null) return "";
  if (typeof window === "string") return window;
  return `${window.start ?? ""}–${window.end ?? ""}`;
}

/**
 * Computes a deterministic hue (0-359) from the role name for avatar tinting.
 */
export function getRoleHue(name: string): number {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash) % 360;
}
