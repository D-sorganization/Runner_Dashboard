/**
 * useRoleContext.ts — role readiness and context-pane data from the staff query layer (#1804).
 *
 * `useRosterReadiness` gives the roster provider availability and schedule holds;
 * `useRoleContext` gives the context pane the selected role's providers, schedule,
 * runs, work items and offline nodes, plus a schedule switch that persists through
 * PUT /roles/{role}/schedule. Desktop and mobile share both, so neither layout can
 * show readiness the backend did not report.
 */
import { useCallback, useMemo } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  staffKeys,
  useResolvedQueryClient,
  useStaffBoard,
  useStaffProviders,
  useStaffRuns,
  useStaffSchedule,
  useStaffWorkItems,
} from "../../hooks/useStaffQueries";
import { errorMessage } from "../Staff/staffApi";
import type { ContextPaneProps, SourceState } from "./contextTypes";
import { putRoleSchedule, type ScheduleRoleReport } from "./roleContextApi";
import { scheduleHold, toRoleDetail, toThreadContext } from "./roleDetail";
import type { StaffRoleItem } from "./types";

function sourceState(query: Pick<UseQueryResult<unknown, Error>, "isError" | "error" | "data">): SourceState {
  if (query.isError) return { error: errorMessage(query.error) };
  return query.data === undefined ? "loading" : "ready";
}

function useScheduleReports(): { reports?: Map<string, ScheduleRoleReport>; state: SourceState } {
  const query = useStaffSchedule();
  const reports = useMemo(() => {
    const roles = query.data?.roles as ScheduleRoleReport[] | undefined;
    return roles ? new Map(roles.map((r) => [r.role, r])) : undefined;
  }, [query.data]);
  return { reports, state: sourceState(query) };
}

export interface RosterReadiness {
  roles: StaffRoleItem[];
  /** Undefined until the providers source answers, so the roster shows "unknown", not "Idle". */
  availableProviders?: Record<string, boolean>;
}

export function useRosterReadiness(roles: StaffRoleItem[]): RosterReadiness {
  const providers = useStaffProviders();
  const { reports } = useScheduleReports();
  const withHolds = useMemo(
    () => (reports ? roles.map((r) => ({ ...r, schedule_hold: scheduleHold(reports.get(r.name)) })) : roles),
    [roles, reports],
  );
  return { roles: withHolds, availableProviders: providers.data?.providers };
}

function scheduleDisabledReason(role: StaffRoleItem, state: SourceState, enabled: boolean | null): string | null {
  if (role.retired) return "Read-only: the role is retired.";
  if (typeof state === "object") return `Schedule status unavailable: ${state.error}`;
  if (state === "loading" || enabled === null) return "Loading schedule status…";
  return null;
}

export function useRoleContext(
  role: StaffRoleItem,
  threadId?: string | null,
): Required<Pick<ContextPaneProps, "role" | "threadContext" | "onToggleSchedule" | "scheduleDisabledReason">> {
  const client = useResolvedQueryClient();
  const providers = useStaffProviders();
  const schedule = useScheduleReports();
  const board = useStaffBoard();
  const runs = useStaffRuns({ role: role.name, limit: 20 });
  const workItems = useStaffWorkItems({ owner_role: role.name });
  const threadWorkItems = useStaffWorkItems({ thread_id: threadId ?? undefined });

  const offline = (board.data as { offline?: unknown } | undefined)?.offline;
  const detail = toRoleDetail(role, {
    availableProviders: providers.data?.providers,
    schedule: schedule.reports?.get(role.name),
    runs: runs.data?.runs,
    workItems: workItems.data?.work_items,
    offlineNodes: Array.isArray(offline) ? offline.map(String) : [],
    sources: {
      providers: sourceState(providers),
      schedule: schedule.state,
      runs: sourceState(runs),
      work_items: sourceState(workItems),
      nodes: sourceState(board),
    },
  });
  const threadContext = threadId
    ? toThreadContext(threadId, runs.data?.runs, threadWorkItems.data?.work_items, {
        runs: sourceState(runs),
        work_items: sourceState(threadWorkItems),
      })
    : null;

  // Persist, then re-read the schedule. A refusal (e.g. 403 without staff.holds.write)
  // rejects, and ContextPane rolls the switch back and shows the reason.
  const onToggleSchedule = useCallback(
    async (roleName: string, enabled: boolean) => {
      await putRoleSchedule(roleName, enabled);
      await client.invalidateQueries({ queryKey: staffKeys.schedule() });
    },
    [client],
  );

  return {
    role: detail,
    threadContext,
    onToggleSchedule,
    scheduleDisabledReason: scheduleDisabledReason(role, schedule.state, detail.schedule?.enabled ?? null),
  };
}
