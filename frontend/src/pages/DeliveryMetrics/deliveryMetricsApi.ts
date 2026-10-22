export interface DeliveryMetricsSummary {
  since: string;
  until: string;
  repo_full: string | null;
  summary: {
    prs: number;
    merged: number;
    median_ci_rounds: number | null;
  };
  weekly_trends: { week_start: string; prs: number; merged: number; median_ci_rounds: number | null }[];
  pr_drill_down: PrRollupRow[];
}

export interface PrRollupRow {
  repo_full: string;
  number: number;
  html_url: string;
  ci_rounds_to_green: number | null;
  lead_time_seconds: number | null;
  ci_wait_seconds: number | null;
  churn_flags: string[];
}

export interface PullDrillDown {
  pull: { repo_full: string; number: number; title: string; author_login: string; html_url: string };
  check_runs: { name: string; status: string; conclusion: string | null; details_url: string }[];
}

const XHR = { "X-Requested-With": "XMLHttpRequest" };

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function assertSummary(value: unknown): DeliveryMetricsSummary {
  const v = value as DeliveryMetricsSummary;
  if (!v?.summary || !Array.isArray(v.weekly_trends) || !Array.isArray(v.pr_drill_down)) {
    throw new Error("Invalid delivery metrics response");
  }
  return v;
}

export async function fetchMetricsSummary(repoFull: string | null): Promise<DeliveryMetricsSummary> {
  const qs = repoFull ? `?repo_full=${encodeURIComponent(repoFull)}` : "";
  const response = await fetch(`/api/v1/metrics/summary${qs}`, { headers: XHR });
  if (response.status === 404) {
    const err = new Error("Delivery metrics are disabled on this node");
    (err as Error & { code?: string }).code = "disabled";
    throw err;
  }
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return assertSummary(await response.json());
}

export async function fetchPullDrillDown(repoFull: string, number: number): Promise<PullDrillDown> {
  const params = new URLSearchParams({ repo_full: repoFull, number: String(number) });
  const response = await fetch(`/api/v1/metrics/pull?${params}`, { headers: XHR });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body = (await response.json()) as PullDrillDown;
  if (!body?.pull || !Array.isArray(body.check_runs)) throw new Error("Invalid pull drill-down response");
  return body;
}

export function uniqueRepos(rows: PrRollupRow[]): string[] {
  return [...new Set(rows.map((r) => r.repo_full))].sort();
}
