// Shared types and helpers for the Queue mobile view.
// Extracted from Mobile.tsx to keep that file under the 500-line cap.

/**
 * Per-run queue-wait vs execution-time breakdown.
 * Populated by GET /api/queue/status (see backend/routers/queue.py).
 */
export interface RunTiming {
  /** Seconds the run spent waiting for a runner to become available. */
  queue_wait_seconds: number;
  /** Seconds the run has been actively executing on a runner. */
  exec_seconds: number;
}

export interface WorkflowRun {
  id: string | number;
  name?: string;
  head_branch?: string;
  html_url?: string;
  run_started_at?: string;
  created_at?: string;
  runner_name?: string;
  runner?: { name?: string };
  triggering_actor?: { login?: string };
  actor?: { login?: string };
  repository?: { name?: string };
  /** Present when fetched from /api/queue/status. */
  timing?: RunTiming;
  stale_reason?: string;
  safe_to_cancel?: boolean;
  current_head_sha?: string;
  run_head_sha?: string;
  pr_number?: number | null;
  age_minutes?: number;
}

export interface StaleCandidate {
  run_id: number;
  workflow: string;
  branch: string;
  url: string;
  repo: string;
  reason: string;
  safe_to_cancel: boolean;
  current_head_sha: string;
  run_head_sha: string;
  pr_number: number | null;
  age_minutes: number;
}

export interface QueueData {
  in_progress?: WorkflowRun[];
  queued?: WorkflowRun[];
  total?: number;
}

export type FilterValue = "all" | "running" | "queued" | "failed" | "stale";

export interface RunDetail {
  run: WorkflowRun;
  status: FilterValue;
  repo: string;
  elapsed: string;
}

export const FILTER_OPTIONS = [
  { label: "All", value: "all" },
  { label: "Running", value: "running" },
  { label: "Queued", value: "queued" },
  { label: "Failed", value: "failed" },
  { label: "Stale", value: "stale" },
];

export const POLL_INTERVAL_MS = 15_000;

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "-";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function elapsedSeconds(run: WorkflowRun): number {
  const start = run.run_started_at ?? run.created_at;
  if (!start) return 0;
  return Math.round((Date.now() - new Date(start).getTime()) / 1000);
}

export function elapsedLabel(run: WorkflowRun): string {
  return formatDuration(elapsedSeconds(run));
}

export function runRepo(run: WorkflowRun): string {
  return run.repository?.name ?? "";
}

export function triggeredBy(run: WorkflowRun): string {
  return run.triggering_actor?.login ?? run.actor?.login ?? "unknown";
}

export function runnerName(run: WorkflowRun): string {
  return run.runner_name ?? run.runner?.name ?? "-";
}

/**
 * Format a run's timing breakdown as a compact string.
 *
 * Returns "Queue: Xm Ys | Exec: Xm Ys" for in-progress runs,
 * "Queue: Xm Ys" for queued runs (exec_seconds === 0),
 * or an empty string when timing data is absent.
 */
export function timingLabel(run: WorkflowRun): string {
  const t = run.timing;
  if (!t) return "";
  const queueStr = formatDuration(t.queue_wait_seconds);
  if (t.exec_seconds === 0) {
    return `Queue: ${queueStr}`;
  }
  const execStr = formatDuration(t.exec_seconds);
  return `Queue: ${queueStr} | Exec: ${execStr}`;
}

export function statusTone(
  status: FilterValue,
): "warning" | "info" | "danger" | "success" | "neutral" {
  if (status === "running") return "warning";
  if (status === "queued") return "info";
  if (status === "failed") return "danger";
  if (status === "stale") return "neutral";
  return "neutral";
}

export function statusLabel(status: FilterValue): string {
  if (status === "running") return "running";
  if (status === "queued") return "queued";
  if (status === "failed") return "failed";
  if (status === "stale") return "stale";
  return "unknown";
}

