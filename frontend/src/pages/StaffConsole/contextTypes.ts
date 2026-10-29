/**
 * contextTypes.ts — Types for the Staff Console Context Pane (SC-D6, Issue #1320).
 */
import type { RosterStatus } from "./types";

/**
 * What this node can say about a provider (#1804). `installed` means the CLI is
 * on PATH; the backend does not report sign-in, so nothing here claims it.
 */
export type ProviderReadiness = "installed" | "not_installed" | "unknown";

export interface RoleProviderStatus {
  name: string;
  readiness: ProviderReadiness;
  node?: string;
}

export interface RoleScheduleInfo {
  cron: string;
  window?: string;
  /** `null` until the schedule source has answered: unknown is never shown as enabled. */
  enabled: boolean | null;
  next_fire?: string | null;
  /** Text of a hold that blocks this role's schedule (#1804). */
  hold?: string | null;
}

/** Runtime model-selector state for the Staff Console dropdown (epic #3295 S2). */
export interface RoleModelInfo {
  /** The role's effective model: the override when set, else the YAML `model` default. */
  effective: string | null;
  /** The runtime override, or `null` when the role runs on its YAML default. */
  override: string | null;
  /** Models the dropdown may select, from GET /roster's `allowed_models`. */
  allowed: string[];
}

export interface RoleBudgetInfo {
  usd_per_day: number;
  /** Not yet served by GET /api/v1/staff/roster; unknown spend renders as "—" (#1744). */
  usd_today?: number;
}

export interface ActiveRunSummary {
  id: string;
  repo: string;
  status: string;
  started_at?: string;
}

export interface RecentWorkItemSummary {
  id: string;
  title: string;
  state: string;
}

/** Load state of one context source: loading, answered, or failed with its message. */
export type SourceState = "loading" | "ready" | { error: string };

export type ContextSource = "providers" | "schedule" | "runs" | "work_items" | "nodes";

export interface RoleReadinessInfo {
  status: RosterStatus;
  reason?: string;
}

export interface RoleDetail {
  name: string;
  title: string;
  mandate: string;
  providers: RoleProviderStatus[];
  schedule?: RoleScheduleInfo;
  model?: RoleModelInfo;
  budget?: RoleBudgetInfo;
  active_runs?: ActiveRunSummary[];
  recent_work_items?: RecentWorkItemSummary[];
  routines?: string[];
  /** Whether the role can execute now, from the same rules as the roster dot (#1804). */
  readiness?: RoleReadinessInfo;
  /** Fleet nodes the board reports offline; their providers and runs are unknown. */
  offline_nodes?: string[];
  sources?: Partial<Record<ContextSource, SourceState>>;
}

export interface ThreadContextData {
  thread_id: string;
  linked_work_items?: RecentWorkItemSummary[];
  linked_runs?: ActiveRunSummary[];
  linked_issues?: string[];
  linked_prs?: string[];
  linked_code_requests?: string[];
  sources?: Partial<Record<"runs" | "work_items", SourceState>>;
}

export interface ContextPaneProps {
  role?: RoleDetail | null;
  threadContext?: ThreadContextData | null;
  /** Persists a schedule change; rejecting rolls the switch back. Absent = read-only. */
  onToggleSchedule?: (roleName: string, enabled: boolean) => Promise<void> | void;
  /** Why the schedule switch is disabled, when it is (#1804). */
  scheduleDisabledReason?: string | null;
  /** Persists a model-override change; rejecting rolls the dropdown back (epic #3295 S2). */
  onChangeModel?: (roleName: string, model: string | null) => Promise<void> | void;
  /** Why the model dropdown is disabled, when it is. */
  modelDisabledReason?: string | null;
  className?: string;
  isLoading?: boolean;
}
