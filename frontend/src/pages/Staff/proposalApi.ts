/**
 * proposalApi.ts — Staff Hub action-proposal preview and decision calls (#1807).
 *
 * Extracted from staffApi.ts to keep modules under 500 lines; re-exported from there.
 */
import { apiRequest } from "../../lib/api";
import { STAFF_BASE } from "./staffBase";

/** The calling principal's read-only approval preview of a proposal (#1807). */
export interface ProposalPreview {
  id: string;
  can_approve: boolean;
  denial_reason: string | null;
  params_hash: string;
}

export function getActionProposal(proposalId: string, signal?: AbortSignal): Promise<ProposalPreview> {
  return apiRequest<ProposalPreview>(`${STAFF_BASE}/proposals/${encodeURIComponent(proposalId)}`, { signal });
}

/** Approving requires `paramsHash`, the hash the approver reviewed; the server answers 428/409 otherwise. */
export function decideActionProposal(
  proposalId: string,
  decision: "approved" | "denied",
  reason?: string,
  execute: boolean = true,
  signal?: AbortSignal,
  paramsHash?: string,
): Promise<unknown> {
  return apiRequest<unknown>(`${STAFF_BASE}/proposals/${encodeURIComponent(proposalId)}/decide`, {
    method: "POST",
    body: { decision, reason, execute, ...(paramsHash ? { params_hash: paramsHash } : {}) },
    signal,
  });
}
