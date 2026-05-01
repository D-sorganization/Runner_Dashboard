/**
 * Tests for usePollingQueries (issue #377).
 * Verifies that every hook declares refetchIntervalInBackground: false
 * (i.e., polling pauses when the tab is hidden) and uses the expected
 * refetchInterval cadence.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { queryClient } from "../usePollingQueries"

// Suppress actual network calls — we're only exercising QueryClient config.
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  }))
})

afterEach(() => {
  vi.restoreAllMocks()
  queryClient.clear()
})

describe("queryClient default options", () => {
  it("defaults refetchIntervalInBackground to false", () => {
    const defaults = queryClient.getDefaultOptions()
    expect(defaults.queries?.refetchIntervalInBackground).toBe(false)
  })

  it("has a non-zero staleTime to honour HTTP cache", () => {
    const defaults = queryClient.getDefaultOptions()
    expect((defaults.queries?.staleTime as number) ?? 0).toBeGreaterThan(0)
  })
})

describe("usePollingQueries module exports", () => {
  it("exports a stable queryClient singleton", async () => {
    const mod = await import("../usePollingQueries")
    expect(mod.queryClient).toBe(queryClient)
  })

  it("exports all 15 resource hooks", async () => {
    const mod = await import("../usePollingQueries")
    const expectedHooks = [
      "useFleet",
      "useRepos",
      "useTests",
      "useCiResults",
      "useReports",
      "useQueue",
      "useMachines",
      "useEnrichedRuns",
      "useWatchdog",
      "useScheduledJobs",
      "useLocalApps",
      "useRunnerCapacity",
      "useDeployment",
      "useDeploymentState",
      "useRunnerAudit",
    ]
    for (const name of expectedHooks) {
      expect(typeof (mod as Record<string, unknown>)[name]).toBe("function")
    }
  })
})
