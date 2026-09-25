/**
 * cardUtils.ts — Pure helper functions for structured message cards (SC-D5, Issue #1319).
 *
 * Separated to satisfy Fast Refresh and maintain modular separation of concerns.
 */

export function formatElapsedTime(seconds?: number): string {
  if (seconds === undefined || seconds === null || isNaN(seconds) || seconds < 0) {
    return "0s";
  }
  const s = Math.floor(seconds);
  if (s < 60) {
    return `${s}s`;
  }
  const mins = Math.floor(s / 60);
  const remSec = s % 60;
  if (mins < 60) {
    return remSec > 0 ? `${mins}m ${remSec}s` : `${mins}m`;
  }
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

export interface BadgeStyle {
  bg: string;
  color: string;
  border: string;
}

export function getRiskBadgeStyle(risk: string): BadgeStyle {
  const norm = (risk || "low").toLowerCase();
  switch (norm) {
    case "read":
    case "low":
      return {
        bg: "rgba(46, 160, 67, 0.15)",
        color: "var(--accent-green, #3fb950)",
        border: "1px solid rgba(46, 160, 67, 0.4)",
      };
    case "medium":
      return {
        bg: "rgba(210, 153, 34, 0.15)",
        color: "var(--accent-yellow, #d29922)",
        border: "1px solid rgba(210, 153, 34, 0.4)",
      };
    case "high":
    case "critical":
    case "owner-only":
      return {
        bg: "rgba(248, 81, 73, 0.15)",
        color: "var(--accent-red, #f85149)",
        border: "1px solid rgba(248, 81, 73, 0.4)",
      };
    default:
      return {
        bg: "rgba(139, 148, 158, 0.15)",
        color: "var(--text-muted, #8b949e)",
        border: "1px solid rgba(139, 148, 158, 0.4)",
      };
  }
}

export function getVerdictBadgeStyle(verdict: string): BadgeStyle {
  const norm = (verdict || "").toUpperCase();
  switch (norm) {
    case "APPROVED":
      return {
        bg: "rgba(46, 160, 67, 0.2)",
        color: "var(--accent-green, #3fb950)",
        border: "1px solid rgba(46, 160, 67, 0.5)",
      };
    case "CHANGES_REQUESTED":
      return {
        bg: "rgba(248, 81, 73, 0.2)",
        color: "var(--accent-red, #f85149)",
        border: "1px solid rgba(248, 81, 73, 0.5)",
      };
    case "COMMENTED":
    default:
      return {
        bg: "rgba(210, 153, 34, 0.2)",
        color: "var(--accent-yellow, #d29922)",
        border: "1px solid rgba(210, 153, 34, 0.5)",
      };
  }
}

export function getStatusBadgeStyle(status: string): BadgeStyle {
  const norm = (status || "").toLowerCase();
  switch (norm) {
    case "running":
    case "in_progress":
      return {
        bg: "rgba(88, 166, 255, 0.15)",
        color: "var(--accent-blue, #58a6ff)",
        border: "1px solid rgba(88, 166, 255, 0.4)",
      };
    case "completed":
    case "success":
    case "approved":
    case "done":
      return {
        bg: "rgba(46, 160, 67, 0.15)",
        color: "var(--accent-green, #3fb950)",
        border: "1px solid rgba(46, 160, 67, 0.4)",
      };
    case "failed":
    case "denied":
    case "error":
      return {
        bg: "rgba(248, 81, 73, 0.15)",
        color: "var(--accent-red, #f85149)",
        border: "1px solid rgba(248, 81, 73, 0.4)",
      };
    case "expired":
    case "cancelled":
      return {
        bg: "rgba(139, 148, 158, 0.15)",
        color: "var(--text-muted, #8b949e)",
        border: "1px solid rgba(139, 148, 158, 0.4)",
      };
    case "queued":
    case "pending":
    case "proposed":
    default:
      return {
        bg: "rgba(210, 153, 34, 0.15)",
        color: "var(--accent-yellow, #d29922)",
        border: "1px solid rgba(210, 153, 34, 0.4)",
      };
  }
}

export function humanizeFailureClass(failureClass?: string | null): string {
  if (!failureClass) return "Execution Error";
  return failureClass
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

export function formatCardDateTime(isoString?: string | null): string {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}
