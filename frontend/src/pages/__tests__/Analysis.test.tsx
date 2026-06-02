// @vitest-environment jsdom
/**
 * Behaviour tests for pages/Analysis.tsx — the self-contained Analysis sub-tab
 * panels extracted from the legacy App.tsx monolith (decomposition #836,
 * pass 7).
 *
 * Each panel fetches its own data via legacyFetch (-> global.fetch), so the
 * tests stub global.fetch and route by URL. Covers:
 *   StatsTab            — stat row, group-by/window controls, row table, trend.
 *   PerformanceTab      — web-vitals table per route, empty state.
 *   AnalysisOutcomesTab — outcome stat row + three result tables, empty states.
 *   ReportsTab          — report list, auto-load latest, markdown render, KPIs.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AnalysisTab,
  StatsTab,
  PerformanceTab,
  AnalysisOutcomesTab,
  ReportsTab,
  type ReportSummary,
} from "../Analysis";

function jsonResponse(body: unknown): Response {
  return { ok: true, json: () => Promise.resolve(body) } as unknown as Response;
}

/** Route fetch responses by URL substring. */
function routeFetch(routes: Array<[string, unknown]>): void {
  global.fetch = vi.fn((url: string) => {
    for (const [needle, body] of routes) {
      if (String(url).includes(needle)) return Promise.resolve(jsonResponse(body));
    }
    return Promise.resolve(jsonResponse({}));
  }) as unknown as typeof fetch;
}

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("StatsTab", () => {
  const ROWS = {
    window_days: 14,
    rows: [
      {
        repo: "Runner_Dashboard",
        workflow_name: "CI Standard",
        count: 12,
        success_rate: 95,
        p50_duration: 90,
        p95_duration: 300,
        p50_queued: 10,
        p95_queued: 40,
      },
    ],
  };

  it("renders the stat row and a row per workflow after fetch", async () => {
    routeFetch([["/api/stats/workflows", ROWS]]);
    render(<StatsTab />);
    await waitFor(() => expect(screen.getByText("CI Standard")).toBeInTheDocument());
    expect(screen.getByText("Tracked workflows")).toBeInTheDocument();
    expect(screen.getByText("95%")).toBeInTheDocument();
    // 90s -> "1.5m" (shown in both the Mean-P50 stat card and the p50 cell).
    expect(screen.getAllByText("1.5m").length).toBeGreaterThan(0);
  });

  it("shows the empty state when no rows are returned", async () => {
    routeFetch([["/api/stats/workflows", { rows: [] }]]);
    render(<StatsTab />);
    await waitFor(() =>
      expect(screen.getByText(/No data yet/)).toBeInTheDocument(),
    );
  });

  it("opens a trend panel when Trend is clicked", async () => {
    routeFetch([
      ["/api/stats/workflows/timeseries", { series: [] }],
      ["/api/stats/workflows", ROWS],
    ]);
    render(<StatsTab />);
    await waitFor(() => expect(screen.getByText("Trend")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Trend"));
    expect(screen.getByText("Close")).toBeInTheDocument();
    expect(screen.getByText(/Not enough data yet/)).toBeInTheDocument();
  });

  it("changing the group-by control refetches", async () => {
    routeFetch([["/api/stats/workflows", ROWS]]);
    render(<StatsTab />);
    await waitFor(() => expect(screen.getByText("CI Standard")).toBeInTheDocument());
    const callsBefore = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.change(screen.getByDisplayValue("Workflow (repo + name)"), {
      target: { value: "repo" },
    });
    await waitFor(() =>
      expect((global.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(
        callsBefore,
      ),
    );
  });
});

describe("PerformanceTab", () => {
  it("renders web-vitals metrics per route", async () => {
    routeFetch([
      [
        "/api/metrics/web-vitals",
        {
          sample_rate: 0.5,
          routes: { "/overview": { LCP: { p50: 1.2, p75: 1.8, p95: 2.5, count: 30 } } },
        },
      ],
    ]);
    render(<PerformanceTab />);
    await waitFor(() => expect(screen.getByText("/overview")).toBeInTheDocument());
    expect(screen.getByText("LCP")).toBeInTheDocument();
    expect(screen.getByText("Sample rate: 50%")).toBeInTheDocument();
  });

  it("shows the empty state when no routes are returned", async () => {
    routeFetch([["/api/metrics/web-vitals", { routes: {} }]]);
    render(<PerformanceTab />);
    await waitFor(() =>
      expect(screen.getByText("No web-vitals data yet.")).toBeInTheDocument(),
    );
  });
});

describe("AnalysisOutcomesTab", () => {
  it("renders outcome stats and result tables", async () => {
    routeFetch([
      [
        "/api/analysis/workflow-machines",
        {
          sample_size: 100,
          success_rate: 88,
          failure_reasons: { timeout: 3, oom: 1 },
          machines: [
            { machine_name: "ControlTower", count: 50, success_rate: 90, avg_duration_seconds: 120 },
          ],
          workflows: [
            {
              key: "rd/ci",
              repo: "Runner_Dashboard",
              workflow_name: "CI",
              count: 40,
              failure: 4,
              avg_duration_seconds: 200,
            },
          ],
          matrix: [
            {
              key: "rd/ci/ct",
              repo: "Runner_Dashboard",
              workflow_name: "CI",
              machine_name: "ControlTower",
              count: 30,
              success_rate: 92,
              avg_duration_seconds: 150,
            },
          ],
        },
      ],
    ]);
    render(<AnalysisOutcomesTab />);
    // "ControlTower" appears in both the machine table and the matrix table.
    await waitFor(() => expect(screen.getAllByText("ControlTower").length).toBeGreaterThan(0));
    expect(screen.getByText("Recent enriched runs")).toBeInTheDocument();
    expect(screen.getByText("88%")).toBeInTheDocument();
    expect(screen.getByText("Machine Health From Jobs")).toBeInTheDocument();
    expect(screen.getByText("Workflow x Machine Runtime")).toBeInTheDocument();
  });

  it("shows empty states when no outcome data is returned", async () => {
    routeFetch([["/api/analysis/workflow-machines", {}]]);
    render(<AnalysisOutcomesTab />);
    await waitFor(() =>
      expect(screen.getByText("No machine placement data yet.")).toBeInTheDocument(),
    );
    expect(screen.getByText("No workflow outcome data yet.")).toBeInTheDocument();
  });
});

describe("ReportsTab", () => {
  const REPORTS: ReportSummary[] = [
    { date: "2026-06-01", size_kb: 12, has_chart: true },
    { date: "2026-05-31", size_kb: 9 },
  ];

  it("lists reports and auto-loads the latest, rendering markdown + KPIs", async () => {
    routeFetch([
      [
        "/api/reports/2026-06-01",
        {
          content: "# Daily Report\n\nAll green.",
          metrics: {
            "PRs Merged (24h)": { value: 5, delta: "+2" },
            "Issues Currently Open": { value: 7 },
            "Fleet Average Score": { value: "B+" },
          },
        },
      ],
    ]);
    render(<ReportsTab reports={REPORTS} loading={false} />);
    // List entries for both reports ("2026-06-01" also shows in the Latest stat).
    expect(screen.getAllByText("2026-06-01").length).toBeGreaterThan(0);
    expect(screen.getByText("2026-05-31")).toBeInTheDocument();
    // KPI cards reflect the auto-loaded latest report.
    await waitFor(() => expect(screen.getByText("PRs Merged")).toBeInTheDocument());
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("B+")).toBeInTheDocument();
    // Rendered markdown content.
    await waitFor(() => expect(screen.getByText("Daily Report")).toBeInTheDocument());
  });

  it("shows the empty placeholder when there are no reports", () => {
    routeFetch([]);
    render(<ReportsTab reports={[]} loading={false} />);
    expect(screen.getByText("No reports found")).toBeInTheDocument();
    expect(screen.getByText("Select a report from the list")).toBeInTheDocument();
  });

  it("loads a different report when its list entry is clicked", async () => {
    routeFetch([
      ["/api/reports/2026-05-31", { content: "older report body", metrics: {} }],
      ["/api/reports/2026-06-01", { content: "latest report body", metrics: {} }],
    ]);
    render(<ReportsTab reports={REPORTS} loading={false} />);
    await waitFor(() => expect(screen.getByText("latest report body")).toBeInTheDocument());
    fireEvent.click(screen.getByText("2026-05-31"));
    await waitFor(() => expect(screen.getByText("older report body")).toBeInTheDocument());
  });
});

describe("AnalysisTab orchestrator", () => {
  it("defaults to the Outcomes sub-tab and renders its panel", async () => {
    localStorage.clear();
    routeFetch([["/api/analysis/outcomes", { rows: [] }]]);
    render(<AnalysisTab runs={[]} runners={[]} reports={[]} />);
    // The SubTabs strip exposes the five sub-tab labels.
    expect(screen.getByText("Outcomes")).toBeInTheDocument();
    expect(screen.getByText("Durations")).toBeInTheDocument();
    expect(screen.getByText("Reports")).toBeInTheDocument();
  });

  it("honours a legacy deep-link activeTab over the stored sub-tab", async () => {
    localStorage.setItem("analysis-subtab", "outcomes");
    routeFetch([["/api/reports", []]]);
    render(<AnalysisTab activeTab="reports" runs={[]} runners={[]} reports={[]} />);
    // Reports panel shows its "select a report" empty hint.
    await waitFor(() =>
      expect(screen.getByText(/Select a report/i)).toBeInTheDocument(),
    );
  });

  it("switches sub-tabs and persists the selection to localStorage", async () => {
    localStorage.clear();
    routeFetch([
      ["/api/analysis/outcomes", { rows: [] }],
      ["/api/stats/workflows", { rows: [] }],
    ]);
    render(<AnalysisTab runs={[]} runners={[]} reports={[]} />);
    fireEvent.click(screen.getByText("Durations"));
    await waitFor(() =>
      expect(localStorage.getItem("analysis-subtab")).toBe("stats"),
    );
  });

  it("badges History and Reports sub-tabs with their counts", () => {
    localStorage.clear();
    routeFetch([["/api/analysis/outcomes", { rows: [] }]]);
    const reports: ReportSummary[] = [
      { date: "2026-06-01", size_kb: 1 },
      { date: "2026-05-31", size_kb: 2 },
    ];
    render(
      <AnalysisTab
        runs={[{ id: 1 }, { id: 2 }, { id: 3 }]}
        runners={[]}
        reports={reports}
      />,
    );
    // History badge = 3 runs, Reports badge = 2 reports.
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
