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

/**
 * Approve or deny a proposal. A handler may resolve `false` when the decision was refused
 * (a viewer's 403, say) so the card re-enables its buttons (#1547).
 */
export type ProposalApproveHandler = (
  proposalId: string,
  params?: Record<string, unknown>,
) => void | Promise<boolean | void>;
export type ProposalDenyHandler = (proposalId: string) => void | Promise<boolean | void>;

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
  dry_run?: boolean | { planned_steps?: string[]; [key: string]: unknown } | null;
  verification_message?: string | null;
  routed_role?: string | null;
}

export type RunStatus =
  | "queued"
  | "running"
  | "needs_input"
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
  /** Set while the run needs input (#1547). */
  question?: string | null;
  summary?: string | null;
  error?: string | null;
  /** Set once the question was answered: who answered and the run that continues it. */
  answered_by?: string | null;
  continued_by?: string | null;
}

/** Cancel a run. Resolves `false` when the cancel was refused, so the card re-enables. */
export type RunCancelHandler = (runId: string) => void | Promise<boolean | void>;
/** Answer a needs-input run. Resolves `false` when the answer was refused (#1547). */
export type RunAnswerHandler = (runId: string, answer: string) => Promise<boolean>;

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
