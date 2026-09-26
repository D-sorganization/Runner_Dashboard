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
import type { components } from "../../lib/api-types";
import type { ThreadInfo, ThreadMessage } from "../StaffConsole/threadTypes";
import type { BriefingResponse, InboxAggregate } from "./inboxTypes";

export { ApiClientError };
export type { BriefingResponse, InboxAggregate };

// ── Shapes (derived directly from OpenAPI generated schemas) ───────────────────

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
export type ConsolidateWhen = components["schemas"]["StaffConsolidateWhen"];
export type RoleStrategy = components["schemas"]["StaffRoleStrategy"];
export type ConsolidationDecision = components["schemas"]["StaffConsolidationDecision"];
export type RoleBudget = components["schemas"]["StaffRoleBudget"];
export type RosterResponse = components["schemas"]["StaffRosterResponse"];
export type RunRecord = components["schemas"]["StaffRunRecord"];
export type RunEvent = components["schemas"]["StaffRunEvent"];
export type RoleLiveness = components["schemas"]["StaffRoleLiveness"];
export type BoardResponse = components["schemas"]["StaffBoardResponse"];
export type RunsResponse = components["schemas"]["StaffRunsResponse"];
export type RunDetailResponse = components["schemas"]["StaffRunDetailResponse"];
export type RunPlan = components["schemas"]["StaffRunPlan"];
export type DispatchResponse = components["schemas"]["StaffDispatchResponse"];
export type CancelResponse = components["schemas"]["StaffCancelResponse"];
export type Hold = components["schemas"]["StaffHold"];
export type HoldsResponse = components["schemas"]["StaffHoldsResponse"];
export type StaffSummaryResponse = components["schemas"]["StaffSummaryResponse"];
export type StaffAuditRecord = components["schemas"]["StaffAuditRecordResponse"];
export type StaffAuditListResponse = components["schemas"]["StaffAuditListResponse"];
export type ScheduleToggleResponse = components["schemas"]["StaffScheduleToggleResponse"];
export type ScheduleResponse = components["schemas"]["StaffScheduleResponse"];
export type UsageResponse = components["schemas"]["StaffUsageResponse"];
export type PricingResponse = components["schemas"]["StaffPricingResponse"];
export type UsageExportResponse = components["schemas"]["StaffUsageExportResponse"];

export type DispatchBody = components["schemas"]["RunBody"];
export type WorkRequest = components["schemas"]["WorkRequest"];
export type RequestTarget = components["schemas"]["RequestTarget"];

/** The `staff.dispatch` action result: `plan` on a dry run, `run_id` on a real dispatch. */
export interface StaffDispatchResult {
  run_id?: string | null;
  role?: string;
  repo?: string;
  status?: string | null;
  machine?: string | null;
  forwarded_to?: string | null;
  dry_run?: boolean;
  plan?: RunPlan | null;
}

/** `POST /api/v1/staff/requests` success body (`staff.work_requests.submit_request`). */
export interface StaffRequestResponse {
  state: "planned" | "executed" | "approval_required";
  kind: string;
  action: string;
  risk?: string;
  params?: Record<string, unknown>;
  thread_id?: string;
  message_id?: string;
  work_item_id?: string;
  proposal_id?: string;
  run_id?: string | null;
  approval?: string;
  plan?: StaffDispatchResult;
  result?: StaffDispatchResult;
}

// ── Calls ────────────────────────────────────────────────────────────────────

export const STAFF_BASE = "/api/v1/staff";

function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).substring(2, 10)}`;
}

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

export function cancelRun(id: string, idempotencyKey?: string): Promise<CancelResponse> {
  return apiRequest<CancelResponse>(`${STAFF_BASE}/runs/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey || generateIdempotencyKey() },
    body: {},
  });
}

export function dispatchRun(role: string, body: DispatchBody, idempotencyKey?: string): Promise<DispatchResponse> {
  return apiRequest<DispatchResponse>(`${STAFF_BASE}/${encodeURIComponent(role)}/run`, {
    body,
    headers: { "Idempotency-Key": idempotencyKey || generateIdempotencyKey() },
  });
}

export function submitStaffRequest(
  body: WorkRequest,
  idempotencyKey?: string,
  signal?: AbortSignal,
): Promise<StaffRequestResponse> {
  return apiRequest<StaffRequestResponse>(`${STAFF_BASE}/requests`, {
    method: "POST",
    body,
    headers: { "Idempotency-Key": idempotencyKey || generateIdempotencyKey() },
    signal,
  });
}

export function fetchHolds(signal?: AbortSignal): Promise<HoldsResponse> {
  return apiRequest<HoldsResponse>(`${STAFF_BASE}/holds`, { signal });
}

export function putHolds(body: { holds: Hold[] } | HoldsResponse, idempotencyKey?: string): Promise<HoldsResponse> {
  return apiRequest<HoldsResponse>(`${STAFF_BASE}/holds`, {
    method: "PUT",
    headers: { "Idempotency-Key": idempotencyKey || generateIdempotencyKey() },
    body,
  });
}

export function fetchStaffInbox(signal?: AbortSignal): Promise<InboxAggregate> {
  return apiRequest<InboxAggregate>("/api/v1/staff/inbox", { signal });
}

export function requestStaffBriefing(
  kind: "morning" | "evening" | "on_demand" = "on_demand",
  signal?: AbortSignal
): Promise<BriefingResponse> {
  return apiRequest<BriefingResponse>("/api/v1/staff/briefing", {
    method: "POST",
    body: { kind },
    signal,
  });
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

// ── Thread & Conversation APIs (SC-B3 #1306, SC-D8 #1331) ───────────────────

/** Threads visible to the caller; `role` keeps those the role participates in. */
export function fetchThreads(
  filter: { role?: string } = {},
  signal?: AbortSignal,
): Promise<{ threads: ThreadInfo[] }> {
  const query = filter.role ? `?role=${encodeURIComponent(filter.role)}` : "";
  return apiRequest<{ threads: ThreadInfo[] }>(`${STAFF_BASE}/threads${query}`, { signal });
}

/** `GET /threads/{id}`: the thread record plus its message history. */
export function fetchThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<{ thread: ThreadInfo; messages: ThreadMessage[] }> {
  return apiRequest<{ thread: ThreadInfo; messages: ThreadMessage[] }>(
    `${STAFF_BASE}/threads/${encodeURIComponent(threadId)}`,
    { signal },
  );
}

/**
 * Message history of a thread. The backend serves history on
 * `GET /threads/{id}`; `/threads/{id}/messages` only accepts POST (#1446).
 */
export function fetchThreadMessages(
  threadId: string,
  signal?: AbortSignal,
): Promise<{ messages: ThreadMessage[] }> {
  return fetchThread(threadId, signal).then(({ messages }) => ({ messages }));
}

export function postThreadMessage(
  threadId: string,
  body: { body_md: string; author?: string; author_kind?: string; meta?: Record<string, unknown> },
  idempotencyKey?: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }
  return apiRequest<unknown>(`${STAFF_BASE}/threads/${encodeURIComponent(threadId)}/messages`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });
}

export function createThread(
  body: { title?: string; kind?: string; participants?: string[]; role?: string },
  signal?: AbortSignal,
): Promise<ThreadInfo> {
  return apiRequest<ThreadInfo>(`${STAFF_BASE}/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}

export function decideActionProposal(
  proposalId: string,
  decision: "approved" | "denied",
  reason?: string,
  execute: boolean = true,
  signal?: AbortSignal,
): Promise<unknown> {
  return apiRequest<unknown>(`${STAFF_BASE}/proposals/${encodeURIComponent(proposalId)}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, reason, execute }),
    signal,
  });
}
