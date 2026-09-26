/**
 * remediationBulkRequest.ts — bulk request construction and response formatting
 * for Remediation Issues and PRs sub-tabs (SC-G5-4, #1500).
 *
 * Produces unified work-requests (`POST /api/v1/staff/requests`) with kinds
 * `issue.act` and `pr.act`, preserving bulk selection, provider, prompt,
 * `force` and `approved_by` semantics.
 */

import { ApiClientError } from "../../lib/api";
import { submitStaffRequest, type StaffRequestResponse, type WorkRequest } from "../Staff/staffApi";

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

/** A dispatch target: one issue or PR in one repository. */
export interface BulkTarget {
  repo: string;
  number: number;
}

export interface BulkByRepoResult {
  outcome: BulkDispatchOutcome;
  /** Targets that were not dispatched, so the caller can keep them selected. */
  failed: BulkTarget[];
}

type BuildRequest = (items: readonly BulkTargetItem[], options: BulkRequestOptions) => WorkRequest;

function groupByRepo(targets: readonly BulkTarget[]): Map<string, BulkTarget[]> {
  const groups = new Map<string, BulkTarget[]>();
  for (const t of targets) groups.set(t.repo, [...(groups.get(t.repo) ?? []), t]);
  return groups;
}

/**
 * Dispatch a selection that may span repositories: one request per repository, since a
 * request targets a single repo. Post: every target is dispatched, awaiting approval, or
 * listed in `failed`; a refused request fails all of its targets with the backend's message.
 */
export async function dispatchBulkByRepo(
  build: BuildRequest,
  targets: readonly BulkTarget[],
  options: BulkRequestOptions,
  kindLabel: string,
  submit: (req: WorkRequest) => Promise<StaffRequestResponse> = (req) => submitStaffRequest(req),
): Promise<BulkByRepoResult> {
  const groups = groupByRepo(targets);
  const parts: { repo: string; outcome: BulkDispatchOutcome }[] = [];
  const failed: BulkTarget[] = [];
  for (const [repo, inRepo] of groups) {
    try {
      const resp = await submit(build(inRepo, options));
      parts.push({ repo, outcome: formatBulkResponseResult(resp, inRepo.length, kindLabel) });
      const rejected = new Set(((resp.result as BulkResultShape | undefined)?.rejected ?? []).map((r) => r.number));
      failed.push(...inRepo.filter((t) => rejected.has(t.number)));
    } catch (err) {
      const text = err instanceof ApiClientError ? err.detail : err instanceof Error ? err.message : String(err);
      parts.push({ repo, outcome: { type: "error", text } });
      failed.push(...inRepo);
    }
  }
  if (parts.length === 1) return { outcome: parts[0].outcome, failed };
  return {
    outcome: {
      type: parts.some((p) => p.outcome.type === "error") ? "error" : "success",
      text: parts.map((p) => `${p.repo}: ${p.outcome.text}`).join(" "),
    },
    failed,
  };
}

/** The selection after a dispatch: only the rows whose target failed, so a retry resends just those. */
export function keepFailedSelected(
  rows: readonly { key: string; target: BulkTarget }[],
  failed: readonly BulkTarget[],
): Record<string, boolean> {
  const isFailed = (t: BulkTarget) => failed.some((f) => f.repo === t.repo && f.number === t.number);
  return Object.fromEntries(rows.filter((r) => isFailed(r.target)).map((r) => [r.key, true]));
}
