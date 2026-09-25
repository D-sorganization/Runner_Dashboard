// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FleetAlertsSection } from "../FleetAlertsSection";

afterEach(() => {
  cleanup();
});

const mockAlerts = [
  {
    id: "hosted-runners" as const,
    level: "warning" as const,
    title: "1 hosted runner violation",
    detail: "Repo D-sorganization/Tools ran on github-hosted runner.",
    contentHash: "hash-1",
  },
];

const mockRunnerAudit = {
  violations: [
    {
      repo: "D-sorganization/Tools",
      workflow: "CI",
      job_name: "test",
      runner_name: "GitHub Actions 2",
      runner_group: "GitHub Actions",
      started_at: "2026-09-25T10:00:00Z",
      run_url: "https://github.com/runs/123",
    },
  ],
  last_checked: "2026-09-25T10:05:00Z",
  error: null,
};

describe("FleetAlertsSection", () => {
  it("renders active fleet alerts and hosted-runner billing violations", () => {
    render(
      <FleetAlertsSection
        alerts={mockAlerts}
        runnerAudit={mockRunnerAudit}
        loading={false}
        error={null}
        onRefreshAudit={vi.fn()}
      />
    );

    expect(screen.getByRole("region", { name: /Fleet Alerts & Billing Audit/i })).toBeInTheDocument();
    expect(screen.getByText("1 hosted runner violation")).toBeInTheDocument();
    expect(screen.getByText("D-sorganization/Tools")).toBeInTheDocument();
    expect(screen.getByText("GitHub Actions 2")).toBeInTheDocument();
  });

  it("triggers audit refresh when refresh button is clicked", () => {
    const onRefreshAudit = vi.fn();
    render(
      <FleetAlertsSection
        alerts={[]}
        runnerAudit={mockRunnerAudit}
        loading={false}
        error={null}
        onRefreshAudit={onRefreshAudit}
      />
    );

    const refreshBtn = screen.getByRole("button", { name: /Refresh Audit/i });
    fireEvent.click(refreshBtn);
    expect(onRefreshAudit).toHaveBeenCalled();
  });

  it("shows clean nominal state when no violations or alerts exist", () => {
    render(
      <FleetAlertsSection
        alerts={[]}
        runnerAudit={{ violations: [], last_checked: "2026-09-25T10:05:00Z", error: null }}
        loading={false}
        error={null}
        onRefreshAudit={vi.fn()}
      />
    );

    expect(screen.getByText(/No active alerts or hosted-runner violations/i)).toBeInTheDocument();
  });

  it("fails visibly with independent error state and retry CTA", () => {
    const onRetry = vi.fn();
    render(
      <FleetAlertsSection
        alerts={[]}
        runnerAudit={{ violations: [] }}
        loading={false}
        error="Failed to load audit: HTTP 500"
        onRetry={onRetry}
        onRefreshAudit={vi.fn()}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load audit: HTTP 500/);
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });
});
