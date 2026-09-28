/**
 * TypeScript types for Waiting on You Inbox & Barb Briefings (SC-C5, Issue #1328).
 */

export type InboxSource =
  | "approval"
  | "needs_input"
  | "escalation"
  | "project_decision"
  | "board_proposal"
  | "auth_sign_in";

export type InboxSeverity = "low" | "medium" | "high" | "critical";

export interface InboxItem {
  id: string;
  source: InboxSource;
  title: string;
  summary: string;
  severity: InboxSeverity;
  created_at: string;
  link: string;
  metadata?: Record<string, unknown>;
  /** Grouped sub-entries (#1725): decision texts, duplicate proposal ids, or per-provider sign-in reasons. */
  details?: InboxDetail[];
}

export type InboxDetail = string | { provider?: string; label?: string; reason?: string };

/** One display line for a grouped inbox detail. */
export function inboxDetailText(detail: InboxDetail): string {
  if (typeof detail === "string") return detail;
  const name = detail.label || detail.provider || "";
  return detail.reason ? `${name}: ${detail.reason}` : name;
}

export interface SourceStatus {
  status: "ok" | "unavailable";
  count: number;
  error?: string | null;
}

export interface InboxAggregate {
  items: InboxItem[];
  count: number;
  counts: {
    total: number;
    approvals: number;
    needs_input: number;
    escalations: number;
    project_decisions: number;
    board_proposals: number;
    auth_sign_ins: number;
  };
  sources: Record<string, SourceStatus>;
  generated_at: string;
}

export interface BriefingResponse {
  ok: boolean;
  briefing_id: string;
  thread_id: string;
  kind: "morning" | "evening" | "on_demand";
  waiting_count: number;
  body_md: string;
}
