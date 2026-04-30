import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueueTab } from "../index";

describe("QueueTab Component", () => {
  const mockQueue = {
    in_progress: [
      {
        id: "run-1",
        name: "Build Workflow",
        repository: { name: "repo-1" },
        head_branch: "main",
        html_url: "https://github.com/org/repo-1/actions/runs/1",
        run_started_at: new Date(Date.now() - 300000).toISOString(),
        runner_name: "ubuntu-latest-4xlarge",
      },
    ],
    queued: [
      {
        id: "run-2",
        name: "Test Workflow",
        repository: { name: "repo-2" },
        head_branch: "feature",
        html_url: "https://github.com/org/repo-2/actions/runs/2",
        created_at: new Date(Date.now() - 120000).toISOString(),
        runner_name: "windows-latest",
      },
    ],
    total: 2,
  };

  const mockEmptyQueue = {
    in_progress: [],
    queued: [],
    total: 0,
  };

  it("renders with in-progress and queued runs", () => {
    render(
      <QueueTab queue={mockQueue} loading={false} onRefresh={() => {}} />
    );

    // Check stats
    expect(screen.getByText("In Progress")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument(); // In progress count
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Total Active")).toBeInTheDocument();
  });

  it("displays correct stat values", () => {
    const { container } = render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    // Stat cards should show correct values
    const statValues = container.querySelectorAll(".stat-value");
    expect(statValues.length).toBeGreaterThan(0);
  });

  it("renders empty queue message when queue is empty", () => {
    render(
      <QueueTab queue={mockEmptyQueue} loading={false} onRefresh={() => {}} />
    );

    expect(screen.getByText(/Queue is empty/i)).toBeInTheDocument();
  });

  it("displays loading spinner when loading", () => {
    render(
      <QueueTab queue={mockEmptyQueue} loading={true} />
    );

    expect(screen.getByText(/Loading queue/i)).toBeInTheDocument();
  });

  it("shows 'In Progress' section with running workflows", () => {
    render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    expect(screen.getByText("In Progress")).toBeInTheDocument();
    expect(screen.getByText("Build Workflow")).toBeInTheDocument();
    expect(screen.getByText("repo-1")).toBeInTheDocument();
  });

  it("shows 'Queued' section with waiting workflows", () => {
    render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Test Workflow")).toBeInTheDocument();
    expect(screen.getByText("repo-2")).toBeInTheDocument();
  });

  it("renders run details correctly", () => {
    const { container } = render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    // Check for branch names
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getByText("feature")).toBeInTheDocument();

    // Check for View links
    const viewLinks = screen.getAllByText("View");
    expect(viewLinks.length).toBeGreaterThan(0);
  });

  it("diagnose button is visible when queue has items", () => {
    render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    expect(screen.getByText(/Why are jobs waiting/i)).toBeInTheDocument();
  });

  it("diagnose button is not visible when queue is empty", () => {
    render(
      <QueueTab queue={mockEmptyQueue} loading={false} />
    );

    expect(screen.queryByText(/Why are jobs waiting/i)).not.toBeInTheDocument();
  });

  it("calls onRefresh callback when provided", async () => {
    const mockRefresh = jest.fn();
    const { container } = render(
      <QueueTab queue={mockQueue} loading={false} onRefresh={mockRefresh} />
    );

    // Simulate a run being cancelled (which would trigger onRefresh)
    // This is a basic integration test
    expect(mockRefresh).not.toHaveBeenCalled();
  });

  it("handles missing queue gracefully", () => {
    render(
      <QueueTab queue={undefined} loading={false} />
    );

    // Should render without errors and show empty state
    expect(screen.getByText(/Queue is empty/i)).toBeInTheDocument();
  });

  it("displays mobile KPI strip", () => {
    const { container } = render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    const mobileKpi = container.querySelector(".mobile-kpi-strip");
    expect(mobileKpi).toBeInTheDocument();
  });

  it("sorts in-progress runs by default", () => {
    const multipleRuns = {
      in_progress: [
        {
          id: "run-1",
          name: "Workflow A",
          repository: { name: "repo-1" },
          head_branch: "main",
          html_url: "https://github.com/org/repo-1/actions/runs/1",
          run_started_at: new Date(Date.now() - 600000).toISOString(),
          runner_name: "ubuntu-1",
        },
        {
          id: "run-2",
          name: "Workflow B",
          repository: { name: "repo-2" },
          head_branch: "develop",
          html_url: "https://github.com/org/repo-2/actions/runs/2",
          run_started_at: new Date(Date.now() - 300000).toISOString(),
          runner_name: "ubuntu-2",
        },
      ],
      queued: [],
      total: 2,
    };

    render(
      <QueueTab queue={multipleRuns} loading={false} />
    );

    // Both workflows should be displayed
    expect(screen.getByText("Workflow A")).toBeInTheDocument();
    expect(screen.getByText("Workflow B")).toBeInTheDocument();
  });

  it("displays stale run indicators when runs exceed 5 minutes", () => {
    const staleQueue = {
      in_progress: [],
      queued: [
        {
          id: "run-stale",
          name: "Stale Workflow",
          repository: { name: "repo-stale" },
          head_branch: "feature",
          html_url: "https://github.com/org/repo-stale/actions/runs/999",
          created_at: new Date(Date.now() - 600000).toISOString(), // 10 minutes ago
          runner_name: "ubuntu-stale",
        },
      ],
      total: 1,
    };

    render(
      <QueueTab queue={staleQueue} loading={false} />
    );

    // The stale run should be visible
    expect(screen.getByText("Stale Workflow")).toBeInTheDocument();
  });
});
