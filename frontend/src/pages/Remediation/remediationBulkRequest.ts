/**
 * remediationBulkRequest.ts — bulk request construction and response formatting
 * for Remediation Issues and PRs sub-tabs (SC-G5-4, #1500).
 *
 * Produces unified work-requests (`POST /api/v1/staff/requests`) with kinds
 * `issue.act` and `pr.act`, preserving bulk selection, provider, prompt,
 * `force` and `approved_by` semantics.
 */

import type { StaffRequestResponse, WorkRequest } from "../Staff/staffApi";

export interface BulkTargetItem {
  repo?: string;
  repository?: string;
  full_name?: string;
  number?: number;
  pr_number?: number;
}

export interface BulkRequestOptions {
  provider?: string;
  prompt?: string;
  force?: boolean;
  approved_by?: string;
}

export interface BulkDispatchOutcome {
  type: "success" | "error";
  text: string;
}

/** Build a unified `issue.act` request with bulk issue targets. */
export function buildBulkIssueRequest(
  items: readonly BulkTargetItem[],
  options: BulkRequestOptions = {},
): WorkRequest {
  const repo = items[0]?.repo || items[0]?.repository || items[0]?.full_name || "";
  const issues = items
    .map((item) => item.number || item.pr_number || 0)
    .filter((num) => num > 0);

  return {
    kind: "issue.act",
    target: {
      repo,
      issues,
      ref: "",
    },
    provider: options.provider || "claude",
    prompt: options.prompt || "",
    force: Boolean(options.force),
    approved_by: options.approved_by || "anonymous",
    dry_run: false,
    machine: "local",
  };
}

/** Build a unified `pr.act` request with bulk PR targets. */
export function buildBulkPRRequest(
  items: readonly BulkTargetItem[],
  options: BulkRequestOptions = {},
): WorkRequest {
  const repo = items[0]?.repo || items[0]?.repository || items[0]?.full_name || "";
  const prs = items
    .map((item) => item.number || item.pr_number || 0)
    .filter((num) => num > 0);

  return {
    kind: "pr.act",
    target: {
      repo,
      prs,
      ref: "",
    },
    provider: options.provider || "claude",
    prompt: options.prompt || "",
    force: Boolean(options.force),
    approved_by: options.approved_by || "anonymous",
    dry_run: false,
    machine: "local",
  };
}

interface PartialFailureTarget {
  number?: number;
  error?: string;
  reason?: string;
}

interface BulkResultShape {
  status?: string;
  accepted?: number;
  dispatched?: unknown[];
  rejected?: PartialFailureTarget[];
}

/**
 * Format the outcome message for bulk dispatch responses, visibly surfacing
 * partial failures per target.
 */
export function formatBulkResponseResult(
  resp: StaffRequestResponse,
  totalRequested: number,
  kindLabel: string,
): BulkDispatchOutcome {
  const result = resp.result as BulkResultShape | undefined;
  const rejected = result?.rejected || [];
  const accepted = typeof result?.accepted === "number" ? result.accepted : totalRequested - rejected.length;

  if (rejected.length > 0) {
    const failures = rejected
      .map((r) => `#${r.number ?? "?"}: ${r.error || r.reason || "dispatch failed"}`)
      .join("; ");
    if (accepted > 0) {
      return {
        type: "error",
        text: `Dispatched ${accepted} of ${totalRequested} ${kindLabel}(s). Failed (${rejected.length}): ${failures}`,
      };
    }
    return {
      type: "error",
      text: `Dispatch failed for all ${totalRequested} ${kindLabel}(s): ${failures}`,
    };
  }

  if (resp.state === "approval_required") {
    return {
      type: "success",
      text: `Dispatch of ${totalRequested} ${kindLabel}(s) submitted and awaiting approval (${resp.approval || "policy gate"}).`,
    };
  }

  return {
    type: "success",
    text: `Dispatched ${totalRequested} ${kindLabel}(s) successfully.`,
  };
}
