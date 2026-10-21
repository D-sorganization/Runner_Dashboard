// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import {
  OperationsCiMetricsSection,
  type CiMetricsResponse,
} from "../OperationsCiMetricsSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const METRICS_FIXTURE: CiMetricsResponse = {
  generated_at: "2026-10-21T12:00:00Z",
  window: {
    days: 14,
    start: "2026-10-07T12:00:00Z",
    end: "2026-10-21T12:00:00Z",
  },
  group_by: "repo",
  sample: { jobs: 80, queue: 80, execution: 78 },
  coverage: {
    complete: true,
    organizations_reported: 1,
    partial_reasons: [],
    caps: [],
    incomplete_runs: 0,
  },
  labels_are_non_additive: false,
  queue_semantics:
    "Queue wait begins at job creation and may include needs dependency wait.",
  rows: [
    {
      key: "Runner_Dashboard",
      job_count: 80,
      queue_count: 80,
      execute_count: 78,
      queue_p50_seconds: 120,
      queue_p95_seconds: 540,
      execute_p50_seconds: 210,
      execute_p95_seconds: 900,
    },
  ],
};

describe("OperationsCiMetricsSection", () => {
  it("renders queue and execution percentiles from the metrics API fixture", () => {
    render(<OperationsCiMetricsSection initialData={METRICS_FIXTURE} />);

    expect(
      screen.getByRole("heading", { name: /CI job metrics/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("row", { name: /Runner_Dashboard/ }),
    ).toHaveTextContent("2m");
    expect(
      screen.getByRole("row", { name: /Runner_Dashboard/ }),
    ).toHaveTextContent("9m");
    expect(
      screen.getByRole("row", { name: /Runner_Dashboard/ }),
    ).toHaveTextContent("3m 30s");
    expect(
      screen.getByRole("row", { name: /Runner_Dashboard/ }),
    ).toHaveTextContent("15m");
    expect(screen.getByText(/80 jobs/)).toBeInTheDocument();
    expect(
      screen.getByText(/Queue P90 target: 10 minutes/i),
    ).toBeInTheDocument();
  });

  it("explains partial coverage and label non-additivity from the API contract", () => {
    render(
      <OperationsCiMetricsSection
        initialData={{
          ...METRICS_FIXTURE,
          group_by: "label",
          labels_are_non_additive: true,
          rows: [
            {
              ...METRICS_FIXTURE.rows[0],
              queue_count: 0,
              queue_p50_seconds: null,
              queue_p95_seconds: null,
            },
          ],
          coverage: {
            ...METRICS_FIXTURE.coverage,
            complete: false,
            incomplete_runs: 2,
            partial_reasons: ["page_cap"],
          },
        }}
      />,
    );

    expect(screen.getByText(/coverage is partial/i)).toBeInTheDocument();
    expect(
      screen.getByText(/label groups are non-additive/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/P90 status is indeterminate/i),
    ).toBeInTheDocument();
  });

  it("uses percentile bounds to report the provisional queue SLO conservatively", () => {
    const { rerender } = render(
      <OperationsCiMetricsSection
        initialData={{
          ...METRICS_FIXTURE,
          rows: [
            {
              ...METRICS_FIXTURE.rows[0],
              queue_p50_seconds: 500,
              queue_p95_seconds: 600,
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText(/P95 bound confirms P90 target/i),
    ).toBeInTheDocument();

    rerender(
      <OperationsCiMetricsSection
        initialData={{
          ...METRICS_FIXTURE,
          rows: [
            {
              ...METRICS_FIXTURE.rows[0],
              queue_p50_seconds: 601,
              queue_p95_seconds: 800,
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText(/P50 bound exceeds P90 target/i),
    ).toBeInTheDocument();
  });

  it("does not make an SLO claim when a group has no queue samples", () => {
    render(
      <OperationsCiMetricsSection
        initialData={{
          ...METRICS_FIXTURE,
          rows: [
            {
              ...METRICS_FIXTURE.rows[0],
              queue_count: 0,
              queue_p50_seconds: null,
              queue_p95_seconds: null,
            },
          ],
        }}
      />,
    );

    expect(
      screen.getByText(/P90 status is indeterminate/i),
    ).toBeInTheDocument();
  });

  it("loads the default repo summary from the merged CI metrics endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(METRICS_FIXTURE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<OperationsCiMetricsSection />);

    expect(await screen.findByText("Runner_Dashboard")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ci/metrics?days=14&group_by=repo",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("fails closed on impossible API counts or percentile values", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          ...METRICS_FIXTURE,
          rows: [
            {
              ...METRICS_FIXTURE.rows[0],
              queue_count: 0,
              queue_p50_seconds: -1,
            },
          ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<OperationsCiMetricsSection />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid CI metrics response",
    );
    expect(
      screen.queryByText(/bound confirms P90 target/i),
    ).not.toBeInTheDocument();
  });
});
