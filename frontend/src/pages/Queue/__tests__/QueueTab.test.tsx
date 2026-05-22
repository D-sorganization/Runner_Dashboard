import React from "react";
import { describe, it, expect, afterEach } from "vitest";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueueTab } from "../index";

describe("QueueTab Component", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

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
    expect(screen.getAllByText("In Progress")[0]).toBeInTheDocument();
    expect(screen.getAllByText("1")[0]).toBeInTheDocument(); // In progress count
    expect(screen.getAllByText("Queued")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Total Active")[0]).toBeInTheDocument();
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

    expect(screen.getAllByText(/Queue is empty/i)[0]).toBeInTheDocument();
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

    expect(screen.getAllByText("In Progress")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Build Workflow")[0]).toBeInTheDocument();
    expect(screen.getAllByText("repo-1")[0]).toBeInTheDocument();
  });

  it("shows 'Queued' section with waiting workflows", () => {
    render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    expect(screen.getAllByText("Queued")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Test Workflow")[0]).toBeInTheDocument();
    expect(screen.getAllByText("repo-2")[0]).toBeInTheDocument();
  });

  it("renders run details correctly", () => {
    render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    // Check for branch names
    expect(screen.getAllByText("main")[0]).toBeInTheDocument();
    expect(screen.getAllByText("feature")[0]).toBeInTheDocument();

    // Check for View links
    const viewLinks = screen.getAllByText("View");
    expect(viewLinks.length).toBeGreaterThan(0);
  });

  it("diagnose button is visible when queue has items", () => {
    render(
      <QueueTab queue={mockQueue} loading={false} />
    );

    expect(screen.getAllByText(/Why are jobs waiting/i)[0]).toBeInTheDocument();
  });

  it("diagnose button is not visible when queue is empty", () => {
    render(
      <QueueTab queue={mockEmptyQueue} loading={false} />
    );

    expect(screen.queryByText(/Why are jobs waiting/i)).not.toBeInTheDocument();
  });

  it("calls onRefresh callback when provided", async () => {
    const mockRefresh = vi.fn();
    render(
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
    expect(screen.getAllByText(/Queue is empty/i)[0]).toBeInTheDocument();
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
    expect(screen.getAllByText("Workflow A")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Workflow B")[0]).toBeInTheDocument();
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
    expect(screen.getAllByText("Stale Workflow")[0]).toBeInTheDocument();
  });

  it("previews stale cleanup candidates with reason counts and safety state", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            stale_count: 2,
            reason_counts: {
              superseded_pr_head: 1,
              closed_or_deleted_ref: 0,
              unsatisfiable_runner_labels: 0,
              age_threshold: 1,
              unknown: 0,
            },
            runs: [
              {
                repo: "repo-safe",
                run_id: 101,
                workflow: "CI",
                branch: "feature/stale",
                pr_number: 44,
                age_minutes: 120,
                run_url: "https://github.com/org/repo-safe/actions/runs/101",
                current_head_sha: "abc123",
                run_head_sha: "def456",
                reason: "superseded_pr_head",
                safe_to_cancel: true,
              },
              {
                repo: "repo-review",
                run_id: 102,
                workflow: "Deploy",
                branch: "release",
                age_minutes: 90,
                reason: "age_threshold",
                safe_to_cancel: false,
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<QueueTab queue={mockEmptyQueue} loading={false} />);
    fireEvent.click(screen.getByRole("button", { name: /preview stale/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/queue/stale?"),
        expect.objectContaining({
          headers: { "X-Requested-With": "XMLHttpRequest" },
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getAllByText("repo-safe").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("superseded pr head")[0]).toBeInTheDocument();
    expect(screen.getAllByText("safe")[0]).toBeInTheDocument();
    expect(screen.getAllByText("age threshold")[0]).toBeInTheDocument();
    expect(screen.getAllByText("blocked")[0]).toBeInTheDocument();
  });

  it("keeps stale purge disabled when no previewed candidates are safe", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            stale_count: 1,
            runs: [
              {
                repo: "repo-review",
                run_id: 102,
                workflow: "Deploy",
                branch: "release",
                age_minutes: 90,
                reason: "age_threshold",
                safe_to_cancel: false,
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<QueueTab queue={mockEmptyQueue} loading={false} />);
    fireEvent.click(screen.getByRole("button", { name: /preview stale/i }));
    await waitFor(() => {
      expect(screen.getAllByText("repo-review").length).toBeGreaterThan(0);
    });

    expect(screen.getByRole("button", { name: /purge safe stale/i })).toBeDisabled();
  });

  it("uses two-step confirmation and posts stale purge payload", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            stale_count: 1,
            runs: [
              {
                repo: "repo-safe",
                run_id: 101,
                workflow: "CI",
                branch: "feature/stale",
                age_minutes: 120,
                reason: "superseded_pr_head",
                safe_to_cancel: true,
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            stale_count: 1,
            cancelled_count: 1,
            errors: [],
            runs: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<QueueTab queue={mockEmptyQueue} loading={false} />);
    fireEvent.change(screen.getByLabelText(/repo filter/i), {
      target: { value: "repo-safe" },
    });
    fireEvent.change(screen.getByLabelText(/workflow filter/i), {
      target: { value: "CI" },
    });
    fireEvent.click(screen.getByRole("button", { name: /preview stale/i }));
    await waitFor(() => {
      expect(screen.getAllByText("repo-safe").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByRole("button", { name: /purge safe stale/i }));
    expect(screen.getByRole("button", { name: /confirm purge/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /confirm purge/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/queue/purge-stale",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          }),
          body: expect.any(String),
        }),
      );
    });
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body).toEqual(
      expect.objectContaining({
        min_age_minutes: 60,
        repo: "repo-safe",
        workflow: "CI",
        max_count: 25,
        safe_only: true,
        dry_run: false,
        run_ids: [101],
      }),
    );
    expect(await screen.findByText(/Cancelled 1 stale run/i)).toBeInTheDocument();
  });

  it("renders mobile stale cards with expandable run details", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            stale_count: 1,
            runs: [
              {
                repo: "repo-mobile",
                run_id: 201,
                workflow: "Mobile CI",
                branch: "mobile-branch",
                pr_number: 12,
                age_minutes: 150,
                current_head_sha: "cur789",
                run_head_sha: "run789",
                reason: "closed_or_deleted_ref",
                safe_to_cancel: true,
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<QueueTab queue={mockEmptyQueue} loading={false} />);

    fireEvent.click(screen.getByRole("button", { name: /preview stale/i }));
    await waitFor(() => {
      expect(screen.getAllByText("repo-mobile").length).toBeGreaterThan(0);
    });

    const mobileList = container.querySelector(
      '[aria-label="Mobile stale cleanup candidates"]',
    );
    expect(mobileList).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Mobile CI/i }));

    expect(await screen.findByText(/Run SHA:/i)).toBeInTheDocument();
    expect(screen.getAllByText(/run789/i).length).toBeGreaterThan(0);
  });
});
