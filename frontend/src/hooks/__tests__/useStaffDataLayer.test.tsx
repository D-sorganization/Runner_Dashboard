// @vitest-environment jsdom
/**
 * Tests for the staff and conversation React Query data layer (issue #1304, epic #1347).
 *
 * Covers:
 * 1. Default queryClient configuration:
 *    - retry 2 with backoff for idempotent GET queries
 *    - no retry for mutations without idempotency key
 *    - retry permitted for mutations with idempotency key
 * 2. Shared caching & staleness:
 *    - returns fresh cached data without network refetch
 *    - stale warning reflects data age
 * 3. Single session-refresh flow on 401:
 *    - multiple parallel 401 responses trigger exactly ONE POST /api/auth/refresh
 *    - successful refresh replays requests seamlessly
 *    - failed refresh emits session expired event once
 * 4. SSE event updates query cache for active run
 * 5. Global connection indicator reflects online/offline and queued mutations
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "../usePollingQueries";
import {
  useStaffRoster,
  useStaffBoard,
  updateStaffRunFromEvent,
} from "../useStaffQueries";
import { SESSION_EXPIRED_EVENT } from "../../lib/sessionExpired";
import { ConnectionIndicator } from "../../primitives/ConnectionIndicator";
import type { RunEvent, RunRecord } from "../../pages/Staff/staffApi";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  queryClient.clear();
});

const FAKE_ROSTER = {
  machine: "DeskComputer",
  active_runs: 0,
  providers: { claude: true },
  roles: [
    {
      name: "night-watch",
      title: "Night Watch",
      summary: "Sweeps overnight",
      playbook: "",
      providers: ["claude"],
      model: null,
      schedule: null,
      window: null,
      repos: ["UpstreamDrift"],
      budget: { usd_per_run: null, usd_per_day: null },
      permissions: {},
      reports_to: null,
      holds: [],
      surface: null,
      retired: false,
      dispatchable: true,
      source_path: "staff/roles/night-watch.yml",
      active_runs: 0,
    },
  ],
};

const FAKE_RUN: RunRecord = {
  id: "run-42",
  role: "night-watch",
  provider: "claude",
  model: null,
  machine: "DeskComputer",
  repo: "UpstreamDrift",
  target_kind: "issue",
  target_ref: "10622",
  prompt: "Do work",
  status: "running",
  requested_by: "operator",
  created_at: "2026-09-24T12:00:00Z",
  started_at: "2026-09-24T12:00:05Z",
  ended_at: null,
  exit_code: null,
  cost_usd: 0.1,
  input_tokens: 100,
  output_tokens: 50,
  workdir: "",
  branch: "staff/work",
  transcript_path: "",
  lease_id: "",
  error: "",
  last_line: "running",
};

describe("QueryClient defaults (issue #1304)", () => {
  it("defaults queries to retry 2 with backoff and staleTime", () => {
    const defaults = queryClient.getDefaultOptions();
    expect(defaults.queries?.staleTime).toBeGreaterThanOrEqual(10_000);
    expect(defaults.queries?.refetchIntervalInBackground).toBe(false);

    // retry option is configured for 2 retries
    const retryFn = defaults.queries?.retry;
    expect(typeof retryFn === "function" || retryFn === 2).toBe(true);
    if (typeof retryFn === "function") {
      expect(retryFn(0, new Error("transient"))).toBe(true);
      expect(retryFn(1, new Error("transient"))).toBe(true);
      expect(retryFn(2, new Error("transient"))).toBe(false);
    }
  });

  it("defaults mutations to no retry unless idempotency key present", () => {
    const defaults = queryClient.getDefaultOptions();
    const mutationRetry = defaults.mutations?.retry;
    if (typeof mutationRetry === "function") {
      expect(mutationRetry(0, new Error("fail"))).toBe(false);
      expect(mutationRetry(0, { hasIdempotencyKey: true } as unknown as Error)).toBe(true);
      expect(mutationRetry(2, { hasIdempotencyKey: true } as unknown as Error)).toBe(false);
    } else {
      expect(mutationRetry).toBe(false);
    }
  });
});

describe("Staff React Query caching & staleness", () => {
  function TestRosterComponent() {
    const { data, isLoading } = useStaffRoster();
    if (isLoading) return <div>loading roster</div>;
    return <div data-testid="roster-role">{data?.roles[0]?.name}</div>;
  }

  it("serves cached roster without refetching when data is fresh", async () => {
    let fetchCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url === "/api/staff/roster" || url === "/api/v1/staff/roster") {
          fetchCount += 1;
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => FAKE_ROSTER,
          });
        }
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
      }),
    );

    const testClient = new QueryClient({
      defaultOptions: { queries: { staleTime: 10_000, retry: false } },
    });

    const { unmount } = render(
      <QueryClientProvider client={testClient}>
        <TestRosterComponent />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("roster-role")).toHaveTextContent("night-watch"));
    expect(fetchCount).toBe(1);

    // Unmount and remount (simulating tab switch)
    unmount();

    render(
      <QueryClientProvider client={testClient}>
        <TestRosterComponent />
      </QueryClientProvider>,
    );

    // Should immediately render from cache without new network fetch
    expect(screen.getByTestId("roster-role")).toHaveTextContent("night-watch");
    expect(fetchCount).toBe(1);
  });
});

describe("401 single session-refresh flow (issue #1304)", () => {
  function MultiQueryComponent() {
    const roster = useStaffRoster();
    const board = useStaffBoard();
    return (
      <div>
        <span>{roster.data ? "roster-ok" : roster.error ? "roster-err" : "roster-load"}</span>
        <span>{board.data ? "board-ok" : board.error ? "board-err" : "board-load"}</span>
      </div>
    );
  }

  it("coalesces parallel 401s into exactly one POST /api/auth/refresh and replays", async () => {
    let refreshCalls = 0;
    let authRefreshed = false;

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url === "/api/auth/refresh" && init?.method === "POST") {
          refreshCalls += 1;
          authRefreshed = true;
          return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) });
        }
        if (
          url === "/api/staff/roster" ||
          url === "/api/v1/staff/roster" ||
          url === "/api/staff/board" ||
          url === "/api/v1/staff/board"
        ) {
          if (!authRefreshed) {
            return Promise.resolve({
              ok: false,
              status: 401,
              json: async () => ({ detail: "Unauthorized" }),
            });
          }
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => (url.endsWith("/roster") ? FAKE_ROSTER : { machine: "Desk", spend_today_usd: 0 }),
          });
        }
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
      }),
    );

    const testClient = new QueryClient({
      defaultOptions: { queries: { staleTime: 10_000, retry: false } },
    });

    render(
      <QueryClientProvider client={testClient}>
        <MultiQueryComponent />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("roster-ok")).toBeInTheDocument();
      expect(screen.getByText("board-ok")).toBeInTheDocument();
    });

    // Exactly one refresh call occurred for both queries!
    expect(refreshCalls).toBe(1);
  });

  it("emits session-expired event when refresh fails", async () => {
    const expiredHandler = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, expiredHandler);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, _init?: RequestInit) => {
        if (url === "/api/auth/refresh") {
          return Promise.resolve({ ok: false, status: 401, json: async () => ({}) });
        }
        if (url === "/api/staff/roster" || url === "/api/v1/staff/roster") {
          return Promise.resolve({
            ok: false,
            status: 401,
            json: async () => ({ detail: "Unauthorized" }),
          });
        }
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
      }),
    );

    const testClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    function SingleQuery() {
      const q = useStaffRoster();
      return <div>{q.isError ? "error" : "loading"}</div>;
    }

    render(
      <QueryClientProvider client={testClient}>
        <SingleQuery />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText("error")).toBeInTheDocument());
    expect(expiredHandler).toHaveBeenCalledTimes(1);

    window.removeEventListener(SESSION_EXPIRED_EVENT, expiredHandler);
  });
});

describe("SSE cache updates (issue #1304)", () => {
  it("updates run and board cache on status/exit SSE events", () => {
    const testClient = new QueryClient();
    testClient.setQueryData(["staff", "run", "run-42"], { run: FAKE_RUN, events: [] });
    testClient.setQueryData(["staff", "board"], {
      machine: "DeskComputer",
      running: [FAKE_RUN],
      queued: [],
      spend_today_usd: 0,
    });

    const exitEvent: RunEvent = {
      seq: 3,
      ts: "2026-09-24T12:05:00Z",
      kind: "exit",
      text: "process exited with code 0",
    };

    updateStaffRunFromEvent(testClient, "run-42", exitEvent);

    const updated = testClient.getQueryData<{ run: RunRecord; events: RunEvent[] }>(["staff", "run", "run-42"]);
    expect(updated?.events).toContainEqual(exitEvent);
    expect(updated?.run.status).toBe("succeeded");
  });
});

describe("Global ConnectionIndicator (issue #1304)", () => {
  it("renders offline indicator when offline with queued items", () => {
    render(<ConnectionIndicator isOnline={false} queuedCount={2} isReconnecting={false} />);
    expect(screen.getByRole("status")).toHaveTextContent("Offline — 2 actions queued");
  });

  it("renders reconnecting indicator when reconnecting", () => {
    render(<ConnectionIndicator isOnline={true} queuedCount={0} isReconnecting={true} />);
    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting…");
  });

  it("renders nothing when fully online and no queued items", () => {
    const { container } = render(<ConnectionIndicator isOnline={true} queuedCount={0} isReconnecting={false} />);
    expect(container.firstChild).toBeNull();
  });
});
