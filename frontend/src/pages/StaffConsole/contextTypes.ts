/**
 * contextTypes.ts — Types for the Staff Console Context Pane (SC-D6, Issue #1320).
 */

export interface RoleProviderStatus {
  name: string;
  signed_in: boolean;
  node?: string;
}

export interface RoleScheduleInfo {
  cron: string;
  window?: string;
  enabled: boolean;
  next_fire?: string | null;
}

export interface RoleBudgetInfo {
  usd_per_day: number;
  usd_today: number;
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

export interface RoleDetail {
  name: string;
  title: string;
  mandate: string;
  providers: RoleProviderStatus[];
  schedule?: RoleScheduleInfo;
  budget?: RoleBudgetInfo;
  active_runs?: ActiveRunSummary[];
  recent_work_items?: RecentWorkItemSummary[];
  routines?: string[];
}

export interface ThreadContextData {
  thread_id: string;
  linked_work_items?: RecentWorkItemSummary[];
  linked_runs?: ActiveRunSummary[];
  linked_issues?: string[];
  linked_prs?: string[];
  linked_code_requests?: string[];
}

export interface ContextPaneProps {
  role?: RoleDetail | null;
  threadContext?: ThreadContextData | null;
  onToggleSchedule?: (roleName: string, enabled: boolean) => Promise<void> | void;
  className?: string;
}
