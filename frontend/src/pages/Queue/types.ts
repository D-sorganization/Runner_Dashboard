// Domain types and pure helpers for the desktop Queue tab.
//
// Extracted from index.tsx during the migration off the legacy
// `React.createElement` / `any` pattern (issue #841) so the view code stays
// strongly typed and under the per-file size convention.

import type { WorkflowRun } from "./mobileTypes";

export type { WorkflowRun } from "./mobileTypes";

/** A queue payload as returned by `GET /api/queue`. */
export interface QueuePayload {
  in_progress?: WorkflowRun[];
  queued?: WorkflowRun[];
  total?: number;
}

export type SortDir = "asc" | "desc";

export interface SortState {
  key: string;
  dir: SortDir;
}

/** Extracts a sortable value from a run for a given accessor key. */
export type RunAccessor = (run: WorkflowRun) => unknown;
export type RunAccessors = Record<string, RunAccessor>;

/** Per-run cancel lifecycle, keyed by `"<repo>/<runId>"`. */
export type CancelState = "pending" | "done" | "error";
export type CancelMap = Record<string, CancelState>;

export interface InlineMessage {
  type: "error" | "success";
  text: string;
}

// ── Stale cleanup ──────────────────────────────────────────────────────────

export const STALE_REASONS = [
  "superseded_pr_head",
  "closed_or_deleted_ref",
  "unsatisfiable_runner_labels",
  "age_threshold",
  "unknown",
] as const;

/** A single stale run row, after normalisation. */
export interface StaleRun {
  repo: string;
  run_id: number | string | null;
  workflow: string;
  branch: string;
  reason: string;
  safe_to_cancel: boolean;
  age_minutes: number | null;
  run_url: string;
  pr_number: number | null;
  current_head_sha: string;
  run_head_sha: string;
}

export interface NormalizedStalePayload {
  org?: string;
  min_age_minutes: number;
  stale_count: number;
  cancelled_count: number;
  errors: string[];
  reason_counts: Record<string, number>;
  runs: StaleRun[];
}

// ── Diagnose ───────────────────────────────────────────────────────────────

export interface RunnerPool {
  busy?: number;
  idle?: number;
  offline?: number;
}

export interface RunnerGroup {
  name: string;
  visibility?: string;
  restricted?: boolean;
  inherited?: boolean;
  runner_count?: number;
  runner_names?: string[];
  allowed_repos?: string[];
  blocked_waiting_repos?: string[];
}

export interface SampledJob {
  repo?: string;
  job?: string;
  target?: string;
  labels?: string[];
}

export interface DiagnosePayload {
  bottleneck?: string;
  runner_pool?: RunnerPool;
  runner_groups?: RunnerGroup[];
  runner_groups_restricted?: boolean;
  pick_runner_misconfig?: unknown[];
  waiting_for_fleet?: number;
  waiting_for_self_hosted?: number;
  waiting_for_generic_self_hosted?: number;
  waiting_for_github_hosted?: number;
  sampled_jobs?: SampledJob[];
  jobs_sampled?: number;
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

/** Computes the next sort state when a sortable column is activated. */
export function sortStateNext(
  current: SortState | null | undefined,
  key: string,
): SortState {
  if (current && current.key === key) {
    return { key, dir: current.dir === "asc" ? "desc" : "asc" };
  }
  return { key, dir: "asc" };
}

/** Coerces a heterogeneous cell value into a comparable primitive. */
export function normalizeSortValue(value: unknown): number | string {
  if (value == null) return "";
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  const text = String(value);
  const asDate = Date.parse(text);
  if (!Number.isNaN(asDate) && /\d{4}-\d{2}-\d{2}|T\d{2}:/.test(text)) {
    return asDate;
  }
  const numeric = Number(text.replace(/[^0-9.-]/g, ""));
  return !Number.isNaN(numeric) ? numeric : text;
}

/** Returns a stable-sorted copy of `rows` per `sort` and `accessors`. */
export function sortRows(
  rows: WorkflowRun[],
  sort: SortState | null | undefined,
  accessors: RunAccessors,
): WorkflowRun[] {
  if (!sort || !sort.key || !accessors[sort.key]) {
    return rows.slice();
  }
  const dir = sort.dir === "desc" ? -1 : 1;
  const accessor = accessors[sort.key];
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const av = normalizeSortValue(accessor(a.row));
      const bv = normalizeSortValue(accessor(b.row));
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return a.index - b.index;
    })
    .map((entry) => entry.row);
}

/** Humanises a stale-reason slug, e.g. `superseded_pr_head` → `superseded pr head`. */
export function formatReason(reason: string): string {
  return String(reason || "unknown").replace(/_/g, " ");
}

interface RawStaleRun {
  repo?: string;
  repository?: { name?: string } | string;
  run_id?: number | string;
  id?: number | string;
  workflow?: string;
  workflow_name?: string;
  name?: string;
  branch?: string;
  head_branch?: string;
  reason?: string;
  safe_to_cancel?: boolean;
  age_minutes?: number | null;
  age?: number | null;
  run_url?: string;
  html_url?: string;
  pr_number?: number | null;
  pull_request_number?: number | null;
  current_head_sha?: string;
  current_pr_head_sha?: string;
  current_sha?: string;
  run_head_sha?: string;
  head_sha?: string;
}

function resolveRepoName(run: RawStaleRun): string {
  if (typeof run.repo === "string" && run.repo) return run.repo;
  if (typeof run.repository === "string" && run.repository) return run.repository;
  if (run.repository && typeof run.repository === "object" && run.repository.name) {
    return run.repository.name;
  }
  return "unknown";
}

/** Normalises a raw API stale run into a fully-populated {@link StaleRun}. */
export function normalizeStaleRun(run: RawStaleRun): StaleRun {
  return {
    repo: resolveRepoName(run),
    run_id: run.run_id ?? run.id ?? null,
    workflow: run.workflow ?? run.workflow_name ?? run.name ?? "?",
    branch: run.branch ?? run.head_branch ?? "?",
    reason: run.reason ?? "unknown",
    safe_to_cancel: run.safe_to_cancel === true,
    age_minutes: run.age_minutes ?? run.age ?? null,
    run_url: run.run_url ?? run.html_url ?? "",
    pr_number: run.pr_number ?? run.pull_request_number ?? null,
    current_head_sha:
      run.current_head_sha ?? run.current_pr_head_sha ?? run.current_sha ?? "",
    run_head_sha: run.run_head_sha ?? run.head_sha ?? "",
  };
}

interface RawStalePayload {
  org?: string;
  min_age_minutes?: number;
  stale_count?: number;
  cancelled_count?: number;
  errors?: string[];
  reason_counts?: Record<string, number>;
  runs?: RawStaleRun[];
  candidates?: RawStaleRun[];
  stale_runs?: RawStaleRun[];
}

/** Normalises a raw stale API payload, filling reason counts and defaults. */
export function normalizeStalePayload(
  payload: RawStalePayload | null | undefined,
): NormalizedStalePayload {
  const rawRuns =
    (payload && (payload.runs || payload.candidates || payload.stale_runs)) || [];
  const runs = rawRuns.map(normalizeStaleRun);
  const reasonCounts: Record<string, number> = {};
  STALE_REASONS.forEach((reason) => {
    reasonCounts[reason] = 0;
  });
  if (payload && payload.reason_counts) {
    Object.keys(payload.reason_counts).forEach((reason) => {
      reasonCounts[reason] = payload.reason_counts?.[reason] || 0;
    });
  } else {
    runs.forEach((run) => {
      const reason = run.reason || "unknown";
      reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
    });
  }
  return {
    org: payload?.org,
    min_age_minutes: payload?.min_age_minutes ?? 60,
    stale_count: payload?.stale_count ?? runs.length,
    cancelled_count: payload?.cancelled_count ?? 0,
    errors: payload?.errors ?? [],
    reason_counts: reasonCounts,
    runs,
  };
}
