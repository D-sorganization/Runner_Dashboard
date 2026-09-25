/**
 * inboxTypes.ts — Type definitions for the Staff Console "Waiting on you" inbox.
 *
 * Implements SC-C5 (Issue #1328) under Epic SC-C (#1349) / Umbrella #1354.
 */

export type WaitingOnYouCategory =
  | "all"
  | "approval"
  | "question"
  | "escalation"
  | "project_decision"
  | "board_proposal"
  | "auth_signin";

export type WaitingOnYouSeverity = "critical" | "high" | "medium" | "low";

export interface WaitingOnYouItem {
  id: string;
  category: string;
  title: string;
  summary: string;
  source: string;
  severity: WaitingOnYouSeverity;
  action_url?: string | null;
  thread_id?: string | null;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface SourceStatus {
  status: "ok" | "unavailable";
  count: number;
  error?: string;
}

export interface WaitingOnYouInboxResponse {
  count: number;
  counts: {
    approvals?: number;
    questions?: number;
    escalations?: number;
    project_decisions?: number;
    board_proposals?: number;
    auth_signins?: number;
    threads?: number;
    [key: string]: number | undefined;
  };
  items: WaitingOnYouItem[];
  sources: Record<string, SourceStatus>;
}

export interface InboxPanelProps {
  inbox?: WaitingOnYouInboxResponse | null;
  isLoading?: boolean;
  onRefresh?: () => void;
  onGenerateBriefing?: (period?: string) => Promise<void> | void;
  isGeneratingBriefing?: boolean;
  onOpenItem?: (item: WaitingOnYouItem) => void;
  className?: string;
}
