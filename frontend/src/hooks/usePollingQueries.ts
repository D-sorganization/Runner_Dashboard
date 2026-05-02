/**
 * usePollingQueries — TanStack Query hooks replacing the 14 setInterval calls
 * in legacy/App.tsx. Each hook declares its own staleTime and refetchInterval;
 * refetchIntervalInBackground: false ensures polling pauses when the tab is
 * hidden, cutting idle bandwidth by ≥ 70 % (issue #377).
 */
import { useQuery, QueryClient } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Shared QueryClient — exported so QueryClientProvider can consume it and
// DevTools can attach to the same instance in development builds.
// ---------------------------------------------------------------------------
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Honour HTTP cache; skip network if data is fresh.
      staleTime: 10_000,
      // Retry once on transient errors before surfacing.
      retry: 1,
      // Pause background polling when tab is hidden.
      refetchIntervalInBackground: false,
    },
  },
})

// ---------------------------------------------------------------------------
// Generic fetch helper — propagates HTTP errors so TanStack Query can retry.
// ---------------------------------------------------------------------------
async function apiFetch<T>(url: string): Promise<T> {
  const resp = await fetch(url, { credentials: "same-origin" })
  if (!resp.ok) throw new Error(`HTTP ${resp.status} from ${url}`)
  return resp.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Resource hooks
// Each corresponds to one or more setInterval calls removed from App.tsx.
// ---------------------------------------------------------------------------

/** Fleet runner status — was t1 = setInterval(fetchFleet, 30000) */
export function useFleet() {
  return useQuery<unknown>({
    queryKey: ["fleet"],
    queryFn: () => apiFetch("/api/fleet"),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })
}

/** Org repos — was t2 = setInterval(fetchRepos, 120000) */
export function useRepos() {
  return useQuery<unknown>({
    queryKey: ["repos"],
    queryFn: () => apiFetch("/api/repos"),
    refetchInterval: 120_000,
    refetchIntervalInBackground: false,
  })
}

/** CI test results — was t3 = setInterval(fetchTests, 120000) */
export function useTests() {
  return useQuery<unknown>({
    queryKey: ["tests"],
    queryFn: () => apiFetch("/api/ci/tests"),
    refetchInterval: 120_000,
    refetchIntervalInBackground: false,
  })
}

/** CI results — was t3b = setInterval(fetchCiResults, 120000) */
export function useCiResults() {
  return useQuery<unknown>({
    queryKey: ["ciResults"],
    queryFn: () => apiFetch("/api/ci/results"),
    refetchInterval: 120_000,
    refetchIntervalInBackground: false,
  })
}

/** Reports — was t4 = setInterval(fetchReports, 300000) */
export function useReports() {
  return useQuery<unknown>({
    queryKey: ["reports"],
    queryFn: () => apiFetch("/api/reports"),
    refetchInterval: 300_000,
    refetchIntervalInBackground: false,
  })
}

/** Queue health — was t5 = setInterval(fetchQueue, 60000) */
export function useQueue() {
  return useQuery<unknown>({
    queryKey: ["queue"],
    queryFn: () => apiFetch("/api/queue"),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })
}

/** Machines / multi-node fleet — was t6 = setInterval(fetchMachines, 60000) */
export function useMachines() {
  return useQuery<unknown>({
    queryKey: ["machines"],
    queryFn: () => apiFetch("/api/machines"),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })
}

/** Enriched workflow runs — was t7 = setInterval(fetchEnrichedRuns, 60000) */
export function useEnrichedRuns() {
  return useQuery<unknown>({
    queryKey: ["enrichedRuns"],
    queryFn: () => apiFetch("/api/runs/enriched"),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })
}

/** Watchdog / health state — was t8 = setInterval(fetchWatchdog, 120000) */
export function useWatchdog() {
  return useQuery<unknown>({
    queryKey: ["watchdog"],
    queryFn: () => apiFetch("/api/watchdog"),
    refetchInterval: 120_000,
    refetchIntervalInBackground: false,
  })
}

/** Scheduled jobs — was t9 = setInterval(fetchScheduledJobs, 300000) */
export function useScheduledJobs() {
  return useQuery<unknown>({
    queryKey: ["scheduledJobs"],
    queryFn: () => apiFetch("/api/scheduled-jobs"),
    refetchInterval: 300_000,
    refetchIntervalInBackground: false,
  })
}

/** Local apps — was t10 = setInterval(fetchLocalApps, 90000) */
export function useLocalApps() {
  return useQuery<unknown>({
    queryKey: ["localApps"],
    queryFn: () => apiFetch("/api/local-apps"),
    refetchInterval: 90_000,
    refetchIntervalInBackground: false,
  })
}

/** Runner capacity — was t11 = setInterval(fetchRunnerCapacity, 60000) */
export function useRunnerCapacity() {
  return useQuery<unknown>({
    queryKey: ["runnerCapacity"],
    queryFn: () => apiFetch("/api/runner-capacity"),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })
}

/** Deployment info — was t12 = setInterval(fetchDeployment, 300000) */
export function useDeployment() {
  return useQuery<unknown>({
    queryKey: ["deployment"],
    queryFn: () => apiFetch("/api/deployment"),
    refetchInterval: 300_000,
    refetchIntervalInBackground: false,
  })
}

/** Deployment state — was t13 = setInterval(fetchDeploymentState, 300000) */
export function useDeploymentState() {
  return useQuery<unknown>({
    queryKey: ["deploymentState"],
    queryFn: () => apiFetch("/api/deployment/state"),
    refetchInterval: 300_000,
    refetchIntervalInBackground: false,
  })
}

/** Runner audit log — was t14 = setInterval(fetchRunnerAudit, 300000) */
export function useRunnerAudit() {
  return useQuery<unknown>({
    queryKey: ["runnerAudit"],
    queryFn: () => apiFetch("/api/runner-audit"),
    refetchInterval: 300_000,
    refetchIntervalInBackground: false,
  })
}
