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
import type { components } from "../../lib/api-types";
import { ApiClientError, apiRequest } from "../../lib/api";

export { ApiClientError };

// ── Shapes derived from generated OpenAPI contract (SC-A10, #1296) ───────────

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

export type RoleSpec = components["schemas"]["StaffRoleSpec"];
export type ConsolidateWhen = {
  open_prs?: number;
  utilisation_pct?: number;
};
export type RoleStrategy = Record<string, unknown> & {
  consolidate_when?: ConsolidateWhen;
};
export type ConsolidationDecision = components["schemas"]["StaffConsolidationDecision"];
export type RosterResponse = components["schemas"]["StaffRosterResponse"];
export type RunRecord = components["schemas"]["StaffRunRecord"];
export type RunEvent = components["schemas"]["StaffRunEvent"];
export type RoleLiveness = components["schemas"]["StaffRoleLiveness"];
export type BoardResponse = components["schemas"]["StaffBoardResponse"];
export type RunsResponse = components["schemas"]["StaffRunsResponse"] & {
  runs: RunRecord[];
};
export type RunDetailResponse = components["schemas"]["StaffRunDetailResponse"] & {
  events: RunEvent[];
};
export type RunPlan = components["schemas"]["StaffRunPlan"] & {
  argv: string[];
};
export type CancelResponse = components["schemas"]["StaffCancelResponse"];
export type Hold = components["schemas"]["StaffHold"] & {
  applies_to: string[];
};
export type HoldsResponse = components["schemas"]["StaffHoldsResponse"] & {
  holds: Hold[];
};
export type StaffAuditEntry = components["schemas"]["StaffAuditEntry"];
export type StaffAuditResponse = components["schemas"]["StaffAuditResponse"];
export type StaffUsageResponse = components["schemas"]["StaffUsageResponse"];
export type StaffPricingResponse = components["schemas"]["StaffPricingResponse"];
export type StaffExportUsageResponse = components["schemas"]["StaffExportUsageResponse"];

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

export type DispatchResponse = components["schemas"]["StaffDispatchResponse"] &
  (
    | { dry_run: true; plan: RunPlan }
    | { dry_run: false; run: RunRecord }
  );

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
  const primary = board.machine || board.hub || "local";
  ensure(primary);
  for (const run of board.running ?? []) ensure(run.machine || primary).running.push(run);
  for (const run of board.queued ?? []) ensure(run.machine || primary).queued.push(run);
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
