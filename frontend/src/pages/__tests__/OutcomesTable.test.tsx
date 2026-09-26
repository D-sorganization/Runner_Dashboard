// @vitest-environment jsdom
/**
 * Outcome scorecard (WP-1.2, #1517): rows and totals share one renderer, a rate with
 * no data behind it shows "—" (never 0%), and each group-by reads its own v1 URL.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OutcomesTable } from "../Staff/OutcomesTable";
import { formatRate, type OutcomeRow, type OutcomesResponse } from "../Staff/staffApi";

const EMPTY_ROW: OutcomeRow = {
  key: "",
  runs: 0,
  succeeded: 0,
  verified: 0,
  failed_verification: 0,
  cost_usd: 0,
  prs: 0,
  prs_unknown: 0,
  merged: 0,
  closed_unmerged: 0,
  open: 0,
  ci_first_pass: 0,
  fix_within_48h: 0,
  merge_rate: null,
  verified_rate: null,
  ci_first_pass_rate: null,
  fix_within_48h_rate: null,
  cost_per_merged_pr: null,
};

const NIGHT_WATCH: OutcomeRow = {
  ...EMPTY_ROW,
  key: "night-watch",
  runs: 10,
  verified: 7,
  cost_usd: 15.5,
  prs: 6,
  prs_unknown: 1,
  merged: 5,
  merge_rate: 0.8333,
  verified_rate: 0.7,
  ci_first_pass_rate: 0.6667,
  fix_within_48h_rate: 0.2,
  cost_per_merged_pr: 3.1,
};

const NO_PRS: OutcomeRow = { ...EMPTY_ROW, key: "sanitation", runs: 4, cost_usd: 4 };

function body(overrides: Partial<OutcomesResponse> = {}): OutcomesResponse {
  return {
    machine: "TestNode",
    group_by: "role",
    since: "2026-09-11T00:00:00Z",
    rows: [NIGHT_WATCH, NO_PRS],
    totals: { ...NIGHT_WATCH, key: "", runs: 14, cost_usd: 19.5 },
    prs_truncated: false,
    ...overrides,
  };
}

function stubFetch(answer: () => Promise<unknown>): string[] {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      urls.push(String(url));
      return answer();
    }),
  );
  return urls;
}

const ok = (payload: unknown) => () =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });

function renderTable() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OutcomesTable />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("OutcomesTable (#1517)", () => {
  it("formats a rate, and shows a dash when there is no data behind it", () => {
    expect(formatRate(0.8333)).toBe("83.3%");
    expect(formatRate(0)).toBe("0.0%");
    expect(formatRate(null)).toBe("—");
    expect(formatRate(undefined)).toBe("—");
    expect(formatRate(Number.NaN)).toBe("—");
  });

  it("renders rows and totals, with dashes for a group that opened no PR", async () => {
    stubFetch(ok(body()));
    renderTable();

    const table = await screen.findByTestId("outcomes-table");
    expect(table).toHaveTextContent("night-watch");
    expect(table).toHaveTextContent("Totals");
    expect(table).toHaveTextContent("83.3%");
    expect(table).toHaveTextContent("6 (1 unread)");
    expect(table).toHaveTextContent("$3.10");
    expect(table).toHaveTextContent("$19.50");
    const sanitation = screen.getByText("sanitation").closest("tr");
    expect(sanitation).toHaveTextContent("—");
    expect(sanitation).not.toHaveTextContent("0.0%");
    expect(screen.queryByTestId("outcomes-truncated")).not.toBeInTheDocument();
  });

  it("reads the v1 endpoint once per group-by", async () => {
    const urls = stubFetch(ok(body()));
    renderTable();
    await screen.findByTestId("outcomes-table");

    fireEvent.click(screen.getByRole("button", { name: "By provider" }));
    fireEvent.click(screen.getByRole("button", { name: "By repo" }));

    await waitFor(() =>
      expect(urls).toEqual([
        "/api/v1/staff/outcomes?group_by=role",
        "/api/v1/staff/outcomes?group_by=provider",
        "/api/v1/staff/outcomes?group_by=repo",
      ]),
    );
    expect(screen.getByRole("button", { name: "By repo" })).toHaveAttribute("aria-pressed", "true");
  });

  it("says when older PRs were not read", async () => {
    stubFetch(ok(body({ prs_truncated: true })));
    renderTable();
    expect(await screen.findByTestId("outcomes-truncated")).toBeInTheDocument();
  });

  it("shows an empty state when no run started in the window", async () => {
    stubFetch(ok(body({ rows: [], totals: EMPTY_ROW })));
    renderTable();
    expect(await screen.findByText("No staff runs")).toBeInTheDocument();
  });

  it("shows an error state when the endpoint fails", async () => {
    stubFetch(() => Promise.reject(new Error("Network failed")));
    renderTable();
    expect(await screen.findByText("Outcome scorecard unavailable")).toBeInTheDocument();
  });
});
