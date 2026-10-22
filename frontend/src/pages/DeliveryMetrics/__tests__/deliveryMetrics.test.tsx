// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import summaryFixture from "../__fixtures__/summary.json";
import { DeliveryMetricsPanel } from "../DeliveryMetricsPanel";
import { useDeliveryMetrics } from "../useDeliveryMetrics";

const PULL_FIXTURE = {
  pull: {
    repo_full: "D-sorganization/Runner_Dashboard",
    number: 2111,
    title: "perf(fleet): bound /api/fleet/status cold path (#2110)",
    author_login: "dieterolson",
    html_url: "https://github.com/D-sorganization/Runner_Dashboard/pull/2111",
  },
  check_runs: [
    { name: "tsc", status: "completed", conclusion: "success", details_url: "https://example.com/tsc" },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mockFetch(map: Record<string, { status: number; body: unknown }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const path = url.split("?")[0];
      const hit = map[path];
      return Promise.resolve({
        ok: hit && hit.status < 300,
        status: hit?.status ?? 500,
        json: () => Promise.resolve(hit?.body ?? {}),
      });
    }),
  );
}

describe("delivery metrics UI", () => {
  it("hook: disabled on 404 and loads fixture", async () => {
    mockFetch({ "/api/v1/metrics/summary": { status: 404, body: {} } });
    const disabled = renderHook(() => useDeliveryMetrics());
    await waitFor(() => expect(disabled.result.current.state.kind).toBe("disabled"));

    mockFetch({
      "/api/v1/metrics/summary": { status: 200, body: summaryFixture },
      "/api/v1/metrics/pull": { status: 200, body: PULL_FIXTURE },
    });
    const ready = renderHook(() => useDeliveryMetrics());
    await waitFor(() => expect(ready.result.current.state.kind).toBe("ready"));
    await ready.result.current.selectPull("D-sorganization/Runner_Dashboard", 2111);
    await waitFor(() => expect(ready.result.current.pullDetail?.pull.number).toBe(2111));
  });

  it("panel: disabled, happy path, invalid JSON", async () => {
    mockFetch({ "/api/v1/metrics/summary": { status: 404, body: {} } });
    render(<DeliveryMetricsPanel />);
    await waitFor(() => expect(screen.getByText(/delivery metrics are disabled/i)).toBeInTheDocument());

    mockFetch({ "/api/v1/metrics/summary": { status: 200, body: summaryFixture } });
    render(<DeliveryMetricsPanel />);
    await waitFor(() => expect(screen.getByTestId("delivery-metrics-panel")).toBeInTheDocument());
    expect(screen.getByText(/not leaderboards/i)).toBeInTheDocument();

    mockFetch({ "/api/v1/metrics/summary": { status: 200, body: {} } });
    render(<DeliveryMetricsPanel />);
    await waitFor(() => expect(screen.getByText(/delivery metrics unavailable/i)).toBeInTheDocument());
  });
});
