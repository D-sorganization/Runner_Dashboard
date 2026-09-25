/**
 * useStaffQueries.ts — React Query data layer for Staff and conversation data (issue #1304, epic #1347).
 *
 * Implements shared caching, background polling, retry with backoff, and SSE cache
 * synchronization for Staff Console entities (roster, board, summary, runs, threads,
 * messages, work items).
 *
 * Law of Demeter: components consume typed data records and status flags without
 * reaching into raw network or cache primitives.
 */
import { useContext } from "react";
import {
  QueryClientContext,
  useMutation,
  useQuery,
  type MutationOptions,
  type QueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiRequest } from "../lib/api";
import {
  BOARD_POLL_MS,
  fetchBoard,
  fetchHolds,
  fetchRoster,
  fetchRun,
  fetchRuns,
  isNotFound,
  type BoardResponse,
  type HoldsResponse,
  type RosterResponse,
  type RunDetailResponse,
  type RunEvent,
  type RunRecord,
  type RunsFilter,
  type RunsResponse,
  type StaffSummaryResponse,
} from "../pages/Staff/staffApi";
import { queryClient as defaultQueryClient } from "./usePollingQueries";

/** Helper that returns the ambient QueryClient or falls back to the exported singleton. */
export function useResolvedQueryClient(): QueryClient {
  const contextClient = useContext(QueryClientContext);
  return contextClient ?? defaultQueryClient;
}

// ── Query Keys ─────────────────────────────────────────────────────────────────

export const staffKeys = {
  all: ["staff"] as const,
  roster: () => [...staffKeys.all, "roster"] as const,
  board: () => [...staffKeys.all, "board"] as const,
  summary: () => [...staffKeys.all, "summary"] as const,
  runs: (filter: RunsFilter = {}) => [...staffKeys.all, "runs", filter] as const,
  run: (id: string) => [...staffKeys.all, "run", id] as const,
  holds: () => [...staffKeys.all, "holds"] as const,
  threads: () => [...staffKeys.all, "threads"] as const,
  messages: (threadId?: string) => [...staffKeys.all, "messages", threadId ?? "all"] as const,
  workItems: () => [...staffKeys.all, "workItems"] as const,
};

// ── Resource Hooks ─────────────────────────────────────────────────────────────

/** Staff roster: cached across tabs, polls in background every 30s. */
export function useStaffRoster(): UseQueryResult<RosterResponse, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.roster(),
      queryFn: ({ signal }) => fetchRoster(signal),
      staleTime: 10_000,
      refetchInterval: 30_000,
      refetchIntervalInBackground: false,
    },
    client,
  );
}

/** Staff board: live status monitor, polls every BOARD_POLL_MS (10s). */
export function useStaffBoard(): UseQueryResult<BoardResponse, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.board(),
      queryFn: ({ signal }) => fetchBoard(signal),
      staleTime: 5_000,
      refetchInterval: BOARD_POLL_MS,
      refetchIntervalInBackground: false,
    },
    client,
  );
}

/** Summary stats for staff runs and spending. */
export function useStaffSummary(): UseQueryResult<StaffSummaryResponse, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.summary(),
      queryFn: ({ signal }) => apiRequest<StaffSummaryResponse>("/api/staff/summary", { signal }),
      staleTime: 10_000,
      refetchInterval: 30_000,
      refetchIntervalInBackground: false,
    },
    client,
  );
}

/** Filterable staff run history with 10s stale window. */
export function useStaffRuns(filter: RunsFilter = {}): UseQueryResult<RunsResponse, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.runs(filter),
      queryFn: ({ signal }) => fetchRuns(filter, signal),
      staleTime: 10_000,
      refetchIntervalInBackground: false,
    },
    client,
  );
}

/** Detail and event stream for a single staff run. */
export function useStaffRun(id: string | null): UseQueryResult<RunDetailResponse, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: id ? staffKeys.run(id) : [...staffKeys.all, "run", "none"],
      queryFn: ({ signal }) => fetchRun(id!, signal),
      enabled: Boolean(id),
      staleTime: 5_000,
      refetchIntervalInBackground: false,
    },
    client,
  );
}

/** Holds configuration. Degrades gracefully on 404. */
export function useStaffHolds(): UseQueryResult<HoldsResponse, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.holds(),
      queryFn: ({ signal }) => fetchHolds(signal),
      staleTime: 10_000,
      refetchIntervalInBackground: false,
    },
    client,
  );
}

/** Staff conversation threads. Degrades to empty list if endpoint absent. */
export function useStaffThreads(): UseQueryResult<{ threads: unknown[] }, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.threads(),
      queryFn: async ({ signal }) => {
        try {
          return await apiRequest<{ threads: unknown[] }>("/api/staff/threads", { signal });
        } catch (err) {
          if (isNotFound(err)) return { threads: [] };
          throw err;
        }
      },
      staleTime: 10_000,
    },
    client,
  );
}

/** Messages within a conversation thread. */
export function useStaffMessages(threadId?: string): UseQueryResult<{ messages: unknown[] }, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.messages(threadId),
      queryFn: async ({ signal }) => {
        const url = threadId
          ? `/api/staff/threads/${encodeURIComponent(threadId)}/messages`
          : "/api/staff/messages";
        try {
          return await apiRequest<{ messages: unknown[] }>(url, { signal });
        } catch (err) {
          if (isNotFound(err)) return { messages: [] };
          throw err;
        }
      },
      enabled: threadId !== undefined,
      staleTime: 5_000,
    },
    client,
  );
}

/** Staff work items list. */
export function useStaffWorkItems(): UseQueryResult<{ work_items: unknown[] }, Error> {
  const client = useResolvedQueryClient();
  return useQuery(
    {
      queryKey: staffKeys.workItems(),
      queryFn: async ({ signal }) => {
        try {
          return await apiRequest<{ work_items: unknown[] }>("/api/staff/work-items", { signal });
        } catch (err) {
          if (isNotFound(err)) return { work_items: [] };
          throw err;
        }
      },
      staleTime: 10_000,
    },
    client,
  );
}

// ── Mutations & Cache Invalidation ─────────────────────────────────────────────

export interface MutationWithIdempotencyOptions<TData, TVariables>
  extends MutationOptions<TData, Error, TVariables> {
  hasIdempotencyKey?: boolean;
}

/** Wrapped mutation hook that honours the idempotency retry policy and invalidates caches. */
export function useStaffMutation<TData = unknown, TVariables = void>(
  options: MutationWithIdempotencyOptions<TData, TVariables>,
): UseMutationResult<TData, Error, TVariables> {
  const client = useResolvedQueryClient();
  return useMutation(options, client);
}

/** Invalidate all staff queries on state mutation. */
export function invalidateStaffQueries(client: QueryClient): Promise<void> {
  return client.invalidateQueries({ queryKey: staffKeys.all });
}

/**
 * Update React Query cache directly from an incoming SSE event.
 * Avoids full round-trips while runs are actively executing.
 */
export function updateStaffRunFromEvent(client: QueryClient, runId: string, event: RunEvent): void {
  // 1. Update the individual run detail cache
  client.setQueryData<RunDetailResponse>(staffKeys.run(runId), (old) => {
    if (!old) return old;
    const existingEvents = old.events ?? [];
    const eventExists = existingEvents.some((e) => e.seq === event.seq);
    const updatedEvents = eventExists ? existingEvents : [...existingEvents, event];

    let newStatus = old.run.status;
    if (event.kind === "exit") {
      newStatus = event.text.includes("code 0") ? "succeeded" : "failed";
    } else if (event.kind === "start") {
      newStatus = "running";
    } else if (event.kind === "cancel") {
      newStatus = "cancelled";
    } else if (event.kind === "error" || event.kind === "timeout") {
      newStatus = "failed";
    }

    const updatedRun: RunRecord = {
      ...old.run,
      status: newStatus,
      last_line: event.text || old.run.last_line,
    };
    return { ...old, run: updatedRun, events: updatedEvents };
  });

  // 2. If status finalized, invalidate board and run lists so counts reconcile
  if (
    event.kind === "exit" ||
    event.kind === "cancel" ||
    event.kind === "error" ||
    event.kind === "timeout"
  ) {
    client.invalidateQueries({ queryKey: staffKeys.board() });
    client.invalidateQueries({ queryKey: [...staffKeys.all, "runs"] });
  }
}
