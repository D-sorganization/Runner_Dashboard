/** Shared labels, constants and pure helpers for the Staff inbox panel (SC-C5).

Split from ``InboxPanel.tsx`` to keep that module under the repo's 500-line
soft cap. Constants and pure helpers only — no components, no JSX.
*/
import type { InboxItem, InboxSource } from "./inboxTypes";

export const MAX_DETAIL_LINES = 5;

export const STORAGE_KEY = "staff_inbox_drawer_open";

export const SOURCE_PRIORITY: Record<string, number> = {
  approval: 1,
  escalation: 2,
  needs_input: 3,
  auth_sign_in: 4,
  project_decision: 5,
  board_proposal: 6,
};

export const KIND_LABEL: Record<InboxSource, string> = {
  approval: "Approval",
  escalation: "Escalation",
  needs_input: "Question",
  auth_sign_in: "Sign-in",
  project_decision: "Decision",
  board_proposal: "Proposal",
};

export const SEVERITY_LABEL: Partial<Record<InboxItem["severity"], string>> = { critical: "Critical", high: "High" };

export function getDecisionRepo(item: InboxItem): string {
  if (typeof item.metadata?.repo === "string" && item.metadata.repo) return item.metadata.repo;
  if (item.link.includes("repo=")) {
    const r = new URLSearchParams(item.link.split("?")[1] || "").get("repo");
    if (r) return r;
  }
  const match = item.title.match(/^\[?([a-zA-Z0-9_.-]+)\]?[:-]/);
  return match ? match[1] : "General";
}