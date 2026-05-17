// Shared types and helpers for the Queue mobile view.
// Extracted from Mobile.tsx to keep that file under the 500-line cap.

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
}

export interface QueueData {
  in_progress?: WorkflowRun[];
  queued?: WorkflowRun[];
  total?: number;
}

export type FilterValue = "all" | "running" | "queued" | "failed";

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

export function statusTone(
  status: FilterValue,
): "warning" | "info" | "danger" | "neutral" {
  if (status === "running") return "warning";
  if (status === "queued") return "info";
  if (status === "failed") return "danger";
  return "neutral";
}

export function statusLabel(status: FilterValue): string {
  if (status === "running") return "running";
  if (status === "queued") return "queued";
  if (status === "failed") return "failed";
  return "unknown";
}
