/**
 * cardTypes.ts — TypeScript data contracts for cards embedded in conversation threads.
 *
 * Implements SC-D5 (Issue #1319) under Epic SC-D (#1350) / Umbrella #1354.
 */

export type ActionRiskLevel =
  | "read"
  | "low"
  | "medium"
  | "high"
  | "critical"
  | "owner-only";

export type ProposalStatus =
  | "pending"
  | "approved"
  | "denied"
  | "executed"
  | "failed"
  | "expired";

export interface ActionProposalData {
  id: string;
  action_name: string;
  target?: string;
  params?: Record<string, unknown>;
  risk_level: ActionRiskLevel;
  status: ProposalStatus;
  proposed_by?: string;
  decided_by?: string | null;
  decided_at?: string | null;
  expires_at?: string | null;
  description?: string;
  execution_result?: Record<string, unknown> | null;
}

export type RunStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface RunCardData {
  id: string;
  run_number?: number | string;
  status: RunStatus;
  node?: string;
  provider?: string;
  elapsed_seconds?: number;
  started_at?: string;
  completed_at?: string | null;
  logs_tail?: string[];
  pr_number?: number | string;
  pr_url?: string;
  run_url?: string;
}

export interface HandoffCardData {
  from_role: string;
  to_role: string;
  reason: string;
  timestamp?: string;
  available_alternatives?: Array<{ name: string; title: string }>;
}

export type ReviewVerdict = "approved" | "changes_requested" | "commented";

export interface ReviewCardData {
  pr_number: number | string;
  pr_title?: string;
  pr_url?: string;
  verdict: ReviewVerdict;
  summary?: string;
  findings?: string[];
}

export interface ErrorCardData {
  failure_class?: string | null;
  cause?: string | null;
  remediation?: string | null;
  node?: string | null;
  retryable?: boolean;
}
