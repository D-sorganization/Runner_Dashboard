/**
 * cardTypes.ts — TypeScript interfaces for Staff Console structured message cards.
 *
 * Implements SC-D5 (Issue #1319) under Epic SC-D (#1350) / Umbrella #1354.
 */

export type RiskLevel = "read" | "low" | "medium" | "high" | "critical" | "owner-only";

export type ProposalState =
  | "proposed"
  | "approved"
  | "denied"
  | "executing"
  | "done"
  | "failed"
  | "expired";

export interface ActionProposalData {
  id: string;
  action: string;
  target?: string;
  risk: RiskLevel | string;
  state: ProposalState | string;
  params: Record<string, unknown>;
  decided_by?: string | null;
  decided_at?: string | null;
  reason?: string | null;
  expires_at?: string | null;
  is_expired?: boolean;
  error?: string | null;
}

export interface ActionCardProps {
  proposal: ActionProposalData;
  onApprove?: (proposalId: string, params: Record<string, unknown>) => Promise<void> | void;
  onDeny?: (proposalId: string, reason?: string) => Promise<void> | void;
  disabled?: boolean;
  className?: string;
}

export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | string;

export interface RunCardData {
  run_id: string;
  status: RunStatus;
  node?: string;
  provider?: string;
  elapsed_seconds?: number;
  started_at?: string;
  log_tail?: string | string[];
  pr?: number | string;
  pr_url?: string;
  run_url?: string;
  can_cancel?: boolean;
}

export interface RunCardProps {
  run: RunCardData;
  onCancelRun?: (runId: string) => Promise<void> | void;
  className?: string;
}

export interface HandoffCardData {
  from_role: string;
  to_role: string;
  reason: string;
  thread_id?: string;
  available_roles?: Array<{ id: string; name: string }>;
}

export interface HandoffCardProps {
  handoff: HandoffCardData;
  onRedirectHandoff?: (targetRole: string) => Promise<void> | void;
  className?: string;
}

export type ReviewVerdict = "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED" | string;

export interface ReviewCardData {
  pr_number?: number | string;
  pr_title?: string;
  pr_url?: string;
  verdict: ReviewVerdict;
  summary?: string;
  findings?: string[];
  reviewer?: string;
  reviewed_at?: string;
}

export interface ReviewCardProps {
  review: ReviewCardData;
  className?: string;
}

export interface ErrorCardData {
  failure_class?: string | null;
  message?: string;
  remediation?: string | null;
  command?: string | null;
  node?: string | null;
  retryable?: boolean;
}

export interface ErrorCardProps {
  error: ErrorCardData;
  onRetry?: () => Promise<void> | void;
  className?: string;
}
