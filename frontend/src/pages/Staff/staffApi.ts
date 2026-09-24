/**
 * staffApi.ts — typed client for the Staff Hub routes (`/api/staff/*`).
 *
 * Issue #1198 (epic #1192). Mirrors the backend contract in
 * `backend/routers/staff.py`: every shape here is a flat record the API
 * returns verbatim (Law of Demeter — the page never reaches into nested
 * runner internals). All requests go through the shared `apiRequest` helper
 * so the CSRF sentinel header (`X-Requested-With: XMLHttpRequest`) and the
 * structured `ApiClientError` contract are applied uniformly.
 */
import { ApiClientError, apiRequest } from "../../lib/api";

export { ApiClientError };

// ── Shapes ───────────────────────────────────────────────────────────────────

export type RunStatus =
  | "queued"
  | "preparing"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "blocked";

/** Statuses for which the run is still alive (mirrors `ACTIVE_STATUSES`). */
export const ACTIVE_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "preparing",
  "running",
]);

export const RUN_STATUSES: readonly RunStatus[] = [
  "queued",
  "preparing",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
];

export interface RoleSpec {
  name: string;
  title: string;
  summary: string;
  playbook: string;
  providers: string[];
  model: string | null;
  schedule: string | null;
  window: string | null;
  repos: string[];
  budget: { usd_per_run: number | null; usd_per_day: number | null };
  permissions: Record<string, unknown>;
  reports_to: string | null;
  holds: string[];
  surface: string | null;
  retired: boolean;
  retired_reason?: string;
  /** Optional `strategy:` block from the role YAML (#1213); absent on older nodes. */
  strategy?: RoleStrategy;
  scope?: Record<string, unknown>;
  prompt_template?: string | null;
  instructions?: string;
  persona?: string;
  chat?: Record<string, unknown>;
  group?: string | null;
  valid?: boolean;
  errors?: string[];
  error?: string | null;
  dispatchable: boolean;
  source_path: string;
  active_runs: number;
}

/** PR-consolidation thresholds (#1213): consolidate when every configured value is met. */
export interface ConsolidateWhen {
  open_prs?: number;
  utilisation_pct?: number;
}

export interface RoleStrategy {
  consolidate_when?: ConsolidateWhen;
}

/** The decision the backend injects into the prompt (#1213). */
export interface ConsolidationDecision {
  mode: "consolidate" | "serial" | string;
  reason: string;
  threshold: ConsolidateWhen;
}

export interface RosterResponse {
  machine: string;
  roles: RoleSpec[];
  providers: Record<string, boolean>;
  active_runs: number;
}

export interface RunRecord {
  id: string;
  role: string;
  provider: string;
  model: string | null;
  machine: string;
  repo: string;
  target_kind: string;
  target_ref: string;
  prompt: string;
  status: RunStatus | string;
  requested_by: string;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  exit_code: number | null;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  workdir: string;
  branch: string;
  transcript_path: string;
  lease_id: string;
  error: string;
  last_line: string;
  /** PR-consolidation strategy (#1213): `consolidate` | `serial` | `` (not applicable). */
  strategy_mode?: string;
  /** Normalised "consolidated N PRs into #M" from the final STAFF_RESULT line (#1213). */
  outcome?: string;
  /** Failure classification (SC-A6, #1297). */
  failure_class?: string;
  /** Whether the failure is transient and eligible for retry. */
  retryable?: boolean;
  /** Actionable remediation instructions for the failure. */
  remediation?: string;
}

export interface RunEvent {
  seq: number;
  ts: string;
  kind: string;
  text: string;
}

/** Liveness of one scheduled role (#1209): `ok | late | dead | never`. */
export interface RoleLiveness {
  role: string;
  schedule: string;
  status: "ok" | "late" | "dead" | "never" | string;
  last_success: string | null;
  last_attempt: string | null;
  last_fired: string | null;
  next_fire: string | null;
  expected_interval_seconds: number | null;
  age_seconds: number | null;
  /** Set on hub `liveness_alerts` entries: the node the row came from. */
  machine?: string;
}

export interface BoardResponse {
  machine: string;
  generated_at: string;
  running: RunRecord[];
  queued: RunRecord[];
  recent: RunRecord[];
  spend_today_usd: Record<string, number>;
  providers: Record<string, boolean>;
  /** Scheduled-role liveness on this node (#1209); absent on older nodes. */
  liveness?: RoleLiveness[];
  /** Hub view only: late/dead scheduled roles across online nodes (#1209). */
  liveness_alerts?: RoleLiveness[];
}

export interface RunsResponse {
  runs: RunRecord[];
  count: number;
}

export interface RunDetailResponse {
  run: RunRecord;
  events: RunEvent[];
}

export interface RunPlan {
  role: string;
  provider: string;
  model: string | null;
  repo: string;
  target_kind: string;
  target_ref: string;
  prompt: string;
  argv: string[];
  branch: string;
  lease_ritual: boolean;
  /** Present when the role has `strategy.consolidate_when` and a repo was given (#1213). */
  consolidation?: ConsolidationDecision | null;
}

export interface DispatchBody {
  provider?: string | null;
  model?: string | null;
  repo: string;
  issue?: number | null;
  pr?: number | null;
  prompt: string;
  machine: string;
  dry_run: boolean;
}

export type DispatchResponse =
  | { dry_run: true; plan: RunPlan; machine: string }
  | { dry_run: false; run: RunRecord; machine: string };

export interface CancelResponse {
  cancelled: boolean;
  run: RunRecord | null;
}

/** Hold shape agreed with the parallel #1196 PR (`PUT /api/staff/holds`). */
export interface Hold {
  id: string;
  text: string;
  set_on: string;
  lifted_when: string;
  applies_to: string[];
  active: boolean;
}

export interface HoldsResponse {
  holds: Hold[];
}

// ── Calls ────────────────────────────────────────────────────────────────────

export const STAFF_BASE = "/api/staff";

export function fetchRoster(signal?: AbortSignal): Promise<RosterResponse> {
  return apiRequest<RosterResponse>(`${STAFF_BASE}/roster`, { signal });
}

export function fetchBoard(signal?: AbortSignal): Promise<BoardResponse> {
  return apiRequest<BoardResponse>(`${STAFF_BASE}/board`, { signal });
}

export interface RunsFilter {
  role?: string;
  status?: string;
  limit?: number;
}

export function fetchRuns(filter: RunsFilter = {}, signal?: AbortSignal): Promise<RunsResponse> {
  const params = new URLSearchParams();
  if (filter.role) params.set("role", filter.role);
  if (filter.status) params.set("status", filter.status);
  if (filter.limit) params.set("limit", String(filter.limit));
  const qs = params.toString();
  return apiRequest<RunsResponse>(`${STAFF_BASE}/runs${qs ? `?${qs}` : ""}`, { signal });
}

export function fetchRun(id: string, signal?: AbortSignal): Promise<RunDetailResponse> {
  return apiRequest<RunDetailResponse>(`${STAFF_BASE}/runs/${encodeURIComponent(id)}`, { signal });
}

export function runStreamUrl(id: string, after = 0): string {
  return `${STAFF_BASE}/runs/${encodeURIComponent(id)}/stream?after=${after}`;
}

export function cancelRun(id: string): Promise<CancelResponse> {
  return apiRequest<CancelResponse>(`${STAFF_BASE}/runs/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: {},
  });
}

export function dispatchRun(role: string, body: DispatchBody): Promise<DispatchResponse> {
  return apiRequest<DispatchResponse>(`${STAFF_BASE}/${encodeURIComponent(role)}/run`, { body });
}

export function fetchHolds(signal?: AbortSignal): Promise<HoldsResponse> {
  return apiRequest<HoldsResponse>(`${STAFF_BASE}/holds`, { signal });
}

export function putHolds(body: HoldsResponse): Promise<HoldsResponse> {
  return apiRequest<HoldsResponse>(`${STAFF_BASE}/holds`, { method: "PUT", body });
}

/** True when the error is a structured 404 from the API (feature absent). */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiClientError && err.status === 404;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiClientError) return err.detail;
  return err instanceof Error ? err.message : String(err);
}

/** Badge tone for a run status (shared by Board, RunLog and RunDetail). */
export function statusTone(status: string): "success" | "warning" | "danger" | "info" | "neutral" {
  switch (status) {
    case "succeeded":
      return "success";
    case "running":
    case "preparing":
      return "info";
    case "queued":
    case "blocked":
      return "warning";
    case "failed":
    case "cancelled":
      return "danger";
    default:
      return "neutral";
  }
}

const warnedFormatUsdInputs = new Set<string>();

export function formatUsd(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    const key = String(value);
    if (!warnedFormatUsdInputs.has(key)) {
      warnedFormatUsdInputs.add(key);
      // eslint-disable-next-line no-console
      console.warn(`[formatUsd] Non-finite or non-number value: ${key}`);
    }
    return "—";
  }
  return `$${value.toFixed(2)}`;
}

export interface SpendSummary {
  total: number | null;
  breakdown: string;
}

/** Parse spend_today_usd into a total and per-provider breakdown (issue #1289). */
export function formatSpendSummary(spend: unknown): SpendSummary {
  if (typeof spend === "number") {
    return {
      total: Number.isFinite(spend) ? spend : null,
      breakdown: "",
    };
  }
  if (typeof spend === "object" && spend !== null) {
    const rec = spend as Record<string, unknown>;
    const providers: [string, number][] = [];
    let total: number | null = null;
    for (const [k, v] of Object.entries(rec)) {
      if (typeof v === "number" && Number.isFinite(v)) {
        if (k === "total") {
          total = v;
        } else {
          providers.push([k, v]);
        }
      }
    }
    if (total === null && providers.length > 0) {
      total = providers.reduce((sum, [, cost]) => sum + cost, 0);
    }
    providers.sort(([a], [b]) => a.localeCompare(b));
    const breakdown = providers.map(([k, v]) => `${k}: ${formatUsd(v)}`).join(" · ");
    return { total, breakdown };
  }
  return { total: null, breakdown: "" };
}

// ── Pure helpers shared by the Staff panels ─────────────────────────────────

export const BOARD_POLL_MS = 10_000;

export interface MachineRow {
  machine: string;
  running: RunRecord[];
  queued: RunRecord[];
}

/** Group running/queued runs by machine, in first-seen order. */
export function groupByMachine(board: BoardResponse): MachineRow[] {
  const rows = new Map<string, MachineRow>();
  const ensure = (machine: string): MachineRow => {
    let row = rows.get(machine);
    if (!row) {
      row = { machine, running: [], queued: [] };
      rows.set(machine, row);
    }
    return row;
  };
  ensure(board.machine);
  for (const run of board.running) ensure(run.machine || board.machine).running.push(run);
  for (const run of board.queued) ensure(run.machine || board.machine).queued.push(run);
  return Array.from(rows.values());
}

/** Late/dead scheduled roles to warn about: the hub list when present, else this node's own rows. */
export function livenessAlerts(board: BoardResponse): RoleLiveness[] {
  if (board.liveness_alerts) return board.liveness_alerts;
  return (board.liveness ?? []).filter((r) => r.status === "late" || r.status === "dead");
}

/** Human label for a run's target (issue / PR / free prompt). */
/** Human form of a role's consolidation threshold, or null when the role has none (#1213). */
export function strategyLabel(role: Pick<RoleSpec, "strategy">): string | null {
  const when = role.strategy?.consolidate_when;
  if (!when) return null;
  const parts: string[] = [];
  if (when.open_prs != null) parts.push(`open PRs ≥ ${when.open_prs}`);
  if (when.utilisation_pct != null) parts.push(`utilisation ≥ ${when.utilisation_pct}%`);
  return parts.length ? `consolidate when ${parts.join(" and ")}` : null;
}

export function targetLabel(run: RunRecord): string {
  if (run.target_kind === "issue") return `#${run.target_ref}`;
  if (run.target_kind === "pr") return `PR #${run.target_ref}`;
  return run.target_ref || "prompt";
}

/** SSE event names the backend emits (runner lifecycle + adapter kinds). */
export const STREAM_EVENT_KINDS: readonly string[] = [
  "queued",
  "clone",
  "worktree",
  "start",
  "exit",
  "error",
  "cancel",
  "timeout",
  "text",
  "json",
  "system",
  "assistant",
  "user",
  "result",
  "message",
  "tool_use",
  "tool_result",
  "item",
  "response",
  "thread",
  "turn",
  "content",
  "status",
  "log",
];
