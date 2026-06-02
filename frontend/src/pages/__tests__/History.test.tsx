// @vitest-environment jsdom
/**
 * Tests for History.tsx — decomposition #836 pass 10.
 *
 * Covers the extracted Analysis "History" sub-tab:
 * 1. Renders a row per run (capped at 50) with workflow/repo/branch/machine.
 * 2. Status filter buttons narrow rows and show live counts.
 * 3. Sortable headers reorder rows.
 * 4. Row click opens the run URL via the trusted-origin guard.
 * 5. Empty-filter message.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HistoryTab } from "../History";

afterEach(cleanup);

const now = Date.now();
const RUNS = [
  {
    id: 1,
    name: "build",
    conclusion: "success",
    status: "completed",
    repository: { name: "alpha" },
    head_branch: "main",
    machine_name: "ControlTower",
    run_started_at: new Date(now - 60000).toISOString(),
    updated_at: new Date(now).toISOString(),
    created_at: new Date(now - 120000).toISOString(),
    html_url: "https://github.com/D-sorganization/alpha/actions/runs/1",
  },
  {
    id: 2,
    name: "deploy",
    conclusion: "failure",
    status: "completed",
    repository: { name: "beta" },
    head_branch: "dev",
    machine_name: "DeskComputer",
    run_started_at: new Date(now - 30000).toISOString(),
    updated_at: new Date(now).toISOString(),
    created_at: new Date(now - 30000).toISOString(),
    html_url: "https://github.com/D-sorganization/beta/actions/runs/2",
  },
  {
    id: 3,
    name: "lint",
    status: "in_progress",
    repository: { name: "gamma" },
    head_branch: "feature",
    machine_name: "ControlTower",
    created_at: new Date(now - 10000).toISOString(),
  },
];

describe("HistoryTab", () => {
  it("renders a row per run with workflow, repo, branch and machine", () => {
    render(<HistoryTab runs={RUNS} />);
    expect(screen.getByText("build")).toBeInTheDocument();
    expect(screen.getByText("deploy")).toBeInTheDocument();
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getAllByText("ControlTower").length).toBe(2);
  });

  it("shows filter buttons with live counts and narrows on click", () => {
    render(<HistoryTab runs={RUNS} />);
    expect(screen.getByText("(3)")).toBeInTheDocument(); // all
    fireEvent.click(screen.getByRole("button", { name: /Failure/ }));
    expect(screen.getByText("deploy")).toBeInTheDocument();
    expect(screen.queryByText("build")).not.toBeInTheDocument();
  });

  it("shows the empty message when a filter matches nothing", () => {
    render(<HistoryTab runs={[RUNS[0]]} />);
    fireEvent.click(screen.getByRole("button", { name: /Cancelled/ }));
    expect(screen.getByText(/No workflow runs match this filter/)).toBeInTheDocument();
  });

  it("reorders rows when a sortable header is clicked", () => {
    render(<HistoryTab runs={RUNS} />);
    const table = screen.getByRole("table");
    // Sort by Workflow ascending: build, deploy, lint
    fireEvent.click(screen.getByText("Workflow"));
    const cells = within(table)
      .getAllByText(/^(build|deploy|lint)$/)
      .map((n) => n.textContent);
    expect(cells).toEqual(["build", "deploy", "lint"]);
  });

  it("opens the run URL via safeOpen on row click", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<HistoryTab runs={[RUNS[0]]} />);
    fireEvent.click(screen.getByText("build"));
    expect(open).toHaveBeenCalledWith(
      "https://github.com/D-sorganization/alpha/actions/runs/1",
      "_blank",
      "noopener,noreferrer",
    );
    open.mockRestore();
  });
});
