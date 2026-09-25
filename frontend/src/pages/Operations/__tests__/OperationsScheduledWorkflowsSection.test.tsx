// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsScheduledWorkflowsSection } from "../OperationsScheduledWorkflowsSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SCHEDULED_DATA = {
  scheduled_workflow_count: 3,
  generated_at: "2026-06-15T12:00:00Z",
  repositories: [
    {
      repository: "UpstreamDrift",
      scheduled_workflow_count: 2,
      workflows: [
        {
          workflow_name: "nightly-eval.yml",
          workflow_path: ".github/workflows/nightly-eval.yml",
          enabled: true,
          cron_expressions: ["0 2 * * *"],
          latest_run: {
            status: "completed",
            conclusion: "success",
            html_url: "https://github.com/D-sorganization/UpstreamDrift/actions/runs/1",
          },
        },
        {
          workflow_name: "weekly-cleanup.yml",
          workflow_path: ".github/workflows/weekly-cleanup.yml",
          enabled: true,
          cron_expressions: ["0 0 * * 0"],
          latest_run: {
            status: "completed",
            conclusion: "failure",
            html_url: "https://github.com/D-sorganization/UpstreamDrift/actions/runs/2",
          },
        },
      ],
    },
    {
      repository: "Runner_Dashboard",
      scheduled_workflow_count: 1,
      workflows: [
        {
          workflow_name: "hourly-soak.yml",
          workflow_path: ".github/workflows/hourly-soak.yml",
          enabled: true,
          cron_expressions: ["0 * * * *"],
          latest_run: null,
        },
      ],
    },
  ],
  dry_run_plan: {
    steps: [
      {
        action: "trigger",
        workflow_name: "nightly-eval.yml",
        repository: "UpstreamDrift",
        reason: "Scheduled cron interval elapsed",
      },
    ],
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("OperationsScheduledWorkflowsSection", () => {
  it("renders section with id=scheduled-workflows and displays workflows list and stats", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(SCHEDULED_DATA));

    render(<OperationsScheduledWorkflowsSection />);

    expect(screen.getByRole("heading", { name: /Scheduled workflows/i })).toBeInTheDocument();
    expect(await screen.findByText("nightly-eval.yml")).toBeInTheDocument();
    expect(screen.getByText("weekly-cleanup.yml")).toBeInTheDocument();
    expect(screen.getByText("hourly-soak.yml")).toBeInTheDocument();
    expect(screen.getAllByText("UpstreamDrift").length).toBeGreaterThan(0);
    expect(screen.getByText("3 workflows")).toBeInTheDocument();
    expect(screen.getByText("success")).toBeInTheDocument();
    expect(screen.getByText("failure")).toBeInTheDocument();
    expect(screen.getByText(/Scheduled cron interval elapsed/i)).toBeInTheDocument();
  });

  it("filters workflows by name or repository", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(SCHEDULED_DATA));

    render(<OperationsScheduledWorkflowsSection />);

    await screen.findByText("nightly-eval.yml");

    const searchInput = screen.getByPlaceholderText(/Filter workflows/i);
    fireEvent.change(searchInput, { target: { value: "hourly" } });

    expect(screen.getByText("hourly-soak.yml")).toBeInTheDocument();
    expect(screen.queryByText("nightly-eval.yml")).not.toBeInTheDocument();
    expect(screen.queryByText("weekly-cleanup.yml")).not.toBeInTheDocument();
  });

  it("shows an error banner with a Retry button when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Service unreachable"));

    render(<OperationsScheduledWorkflowsSection />);

    expect(
      await screen.findByText(/Failed to load scheduled workflows: Service unreachable/i),
    ).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();
  });
});
