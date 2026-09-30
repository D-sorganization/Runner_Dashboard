/**
 * roleContextApi.ts — typed calls for the role readiness the Staff Console shows (#1804).
 *
 * Every route here already exists on the backend; this module only gives the
 * console a typed client for them:
 *   GET /api/v1/staff/providers              provider id -> installed on this node
 *   GET /api/v1/staff/schedule               per role: enabled, blocking hold, next fire
 *   PUT /api/v1/staff/roles/{role}/schedule  enable/disable a role's schedule (staff.holds.write)
 *   GET /api/v1/staff/work-items             work items by owner role or thread
 */
import { apiRequest } from "../../lib/api";
import { STAFF_BASE, type ScheduleResponse } from "../Staff/staffApi";

/** `GET /providers`: whether each provider's CLI is installed on this node. It says nothing about sign-in. */
export interface ProvidersResponse {
  providers: Record<string, boolean>;
}

/** One role's gate report from `GET /schedule` (`StaffScheduler.evaluate`). */
export interface ScheduleRoleReport {
  role: string;
  schedule?: string | null;
  enabled?: boolean;
  /** A blocking hold's text, or the scheduler's "schedule disabled for role" marker. */
  hold?: string | null;
  next_fire?: string | null;
  schedule_error?: string;
}

export interface WorkItemRecord {
  id: string;
  title: string;
  state: string;
  thread_id?: string;
  owner_role?: string;
}

export interface WorkItemsFilter {
  owner_role?: string;
  thread_id?: string;
}

/** The scheduler's `hold` value when the role's own schedule is switched off, not a hold. */
export const SCHEDULE_DISABLED_MARKER = "schedule disabled for role";

export function fetchStaffProviders(signal?: AbortSignal): Promise<ProvidersResponse> {
  return apiRequest<ProvidersResponse>(`${STAFF_BASE}/providers`, { signal });
}

export function fetchStaffSchedule(signal?: AbortSignal): Promise<ScheduleResponse> {
  return apiRequest<ScheduleResponse>(`${STAFF_BASE}/schedule`, { signal });
}

export function putRoleSchedule(role: string, enabled: boolean): Promise<unknown> {
  return apiRequest<unknown>(`${STAFF_BASE}/roles/${encodeURIComponent(role)}/schedule`, {
    method: "PUT",
    body: { enabled, reason: "Staff Console schedule switch" },
  });
}

export function fetchWorkItems(filter: WorkItemsFilter, signal?: AbortSignal): Promise<{ work_items: WorkItemRecord[] }> {
  const params = new URLSearchParams();
  if (filter.owner_role) params.set("owner_role", filter.owner_role);
  if (filter.thread_id) params.set("thread_id", filter.thread_id);
  params.set("limit", "20");
  return apiRequest<{ work_items: WorkItemRecord[] }>(`${STAFF_BASE}/work-items?${params.toString()}`, { signal });
}
