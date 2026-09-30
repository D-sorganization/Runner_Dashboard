/**
 * roleDetail.ts — build the context pane's role and thread records from backend data (#1804).
 *
 * Pure functions: every field comes from a source the caller loaded. A source
 * that has not answered stays unknown (`enabled: null`, `readiness: "unknown"`,
 * `sources.<x> = "loading"`), so nothing is shown as ready or enabled by default.
 */
import { ACTIVE_STATUSES, type RunRecord } from "../Staff/staffApi";
import type {
  ActiveRunSummary,
  ProviderReadiness,
  RecentWorkItemSummary,
  RoleDetail,
  SourceState,
  ThreadContextData,
} from "./contextTypes";
import { SCHEDULE_DISABLED_MARKER, type ScheduleRoleReport, type WorkItemRecord } from "./roleContextApi";
import { computeRoleStatus, formatRoleWindow } from "./rosterUtils";
import type { StaffRoleItem } from "./types";

export interface RoleSources {
  /** Provider id -> installed on this node; undefined while unknown. */
  availableProviders?: Record<string, boolean>;
  /** This role's schedule report; undefined while unknown. */
  schedule?: ScheduleRoleReport;
  runs?: RunRecord[];
  workItems?: WorkItemRecord[];
  offlineNodes?: string[];
  sources?: RoleDetail["sources"];
}

/** A report's blocking hold; the scheduler's own "disabled" marker is not a hold. */
export function scheduleHold(report: ScheduleRoleReport | undefined): string | null {
  const hold = report?.hold;
  return hold && hold !== SCHEDULE_DISABLED_MARKER ? hold : null;
}

function providerReadiness(name: string, available?: Record<string, boolean>): ProviderReadiness {
  const value = available?.[name];
  if (value === true) return "installed";
  if (value === false) return "not_installed";
  return "unknown";
}

export function toRunSummary(run: RunRecord): ActiveRunSummary {
  return {
    id: run.id,
    repo: run.repo ?? "",
    status: run.status,
    started_at: run.started_at ?? undefined,
  };
}

export function toWorkItemSummary(item: WorkItemRecord): RecentWorkItemSummary {
  return { id: item.id, title: item.title, state: item.state };
}

export function toRoleDetail(role: StaffRoleItem, src: RoleSources = {}): RoleDetail {
  const hold = scheduleHold(src.schedule);
  const withHold: StaffRoleItem = { ...role, schedule_hold: role.schedule_hold ?? hold };
  return {
    name: role.name,
    title: role.title,
    mandate: role.summary || "",
    providers: (Array.isArray(role.providers) ? role.providers : []).map((name) => ({
      name,
      readiness: providerReadiness(name, src.availableProviders),
    })),
    budget: role.budget
      ? { usd_per_day: role.budget.usd_per_day ?? 0, usd_today: role.budget.spend_today }
      : undefined,
    schedule: role.schedule
      ? {
          cron: role.schedule,
          window: formatRoleWindow(role.window) || undefined,
          enabled: typeof src.schedule?.enabled === "boolean" ? src.schedule.enabled : null,
          next_fire: src.schedule?.next_fire ?? null,
          hold,
        }
      : undefined,
    active_runs: (src.runs ?? []).filter((r) => ACTIVE_STATUSES.has(r.status)).map(toRunSummary),
    recent_work_items: (src.workItems ?? []).map(toWorkItemSummary),
    readiness: computeRoleStatus(withHold, src.availableProviders),
    offline_nodes: src.offlineNodes ?? [],
    sources: src.sources,
  };
}

/** Thread context: the runs and work items that name this thread. */
export function toThreadContext(
  threadId: string,
  runs: RunRecord[] | undefined,
  workItems: WorkItemRecord[] | undefined,
  sources: { runs: SourceState; work_items: SourceState },
): ThreadContextData {
  return {
    thread_id: threadId,
    linked_runs: (runs ?? []).filter((r) => String(r.thread_id ?? "") === threadId).map(toRunSummary),
    linked_work_items: (workItems ?? []).map(toWorkItemSummary),
    sources,
  };
}
