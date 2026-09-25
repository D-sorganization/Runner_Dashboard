import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { ContextPane } from "../ContextPane";
import type { RoleDetail, ThreadContextData } from "../contextTypes";

const mockRole: RoleDetail = {
  name: "night-watch",
  title: "Overnight Watchdog",
  mandate: "Monitor fleet health and clean up stalled runners overnight.",
  providers: [
    { name: "claude", signed_in: true, node: "ControlTower" },
    { name: "codex", signed_in: true, node: "DeskComputer" },
  ],
  schedule: {
    cron: "0 22 * * *",
    window: "22:00 - 06:00",
    enabled: true,
    next_fire: "2026-09-25T22:00:00Z",
  },
  budget: {
    usd_per_day: 15.0,
    usd_today: 3.5,
  },
  active_runs: [
    { id: "run_nw_01", repo: "UpstreamDrift", status: "running", started_at: "2026-09-25T04:30:00Z" },
  ],
  recent_work_items: [
    { id: "wi_101", title: "Drain offline node", state: "done" },
  ],
  routines: ["Stalled run sweep", "Ghost runner cleanup"],
};

const mockThreadContext: ThreadContextData = {
  thread_id: "th_nw_123",
  linked_work_items: [
    { id: "wi_102", title: "Review runner lease", state: "in_progress" },
  ],
  linked_runs: [
    { id: "run_nw_01", repo: "UpstreamDrift", status: "running" },
  ],
  linked_issues: ["#1322", "#1321"],
  linked_prs: ["PR #1410"],
  linked_code_requests: ["CR-98"],
};

describe("ContextPane (SC-D6, Issue #1320)", () => {
  it("renders role details including mandate, providers, budget, and active runs", () => {
    render(
      <ContextPane
        role={mockRole}
        threadContext={mockThreadContext}
        onToggleSchedule={vi.fn()}
      />
    );

    expect(screen.getByText("Overnight Watchdog")).not.toBeNull();
    expect(screen.getByText(/Monitor fleet health/i)).not.toBeNull();
    expect(screen.getByText("claude")).not.toBeNull();
    expect(screen.getByText(/3.50/)).not.toBeNull();
    expect(screen.getByText(/15.00/)).not.toBeNull();
    expect(screen.getByText("run_nw_01")).not.toBeNull();
  });

  it("toggles role schedule switch and calls onToggleSchedule", async () => {
    const handleToggle = vi.fn().mockResolvedValue(undefined);
    render(
      <ContextPane
        role={mockRole}
        threadContext={mockThreadContext}
        onToggleSchedule={handleToggle}
      />
    );

    const toggle = screen.getByRole("switch", { name: /toggle schedule/i });
    expect(toggle.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(toggle);
    expect(handleToggle).toHaveBeenCalledWith("night-watch", false);
  });

  it("reverts switch and displays error when schedule toggle fails", async () => {
    const handleToggle = vi.fn().mockRejectedValue(new Error("Permission denied: staff.holds.write required"));
    render(
      <ContextPane
        role={mockRole}
        threadContext={mockThreadContext}
        onToggleSchedule={handleToggle}
      />
    );

    const toggle = screen.getByRole("switch", { name: /toggle schedule/i });
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByRole("alert")).not.toBeNull();
      expect(screen.getByText(/Permission denied/i)).not.toBeNull();
      // Switch reverted back to true
      expect(toggle.getAttribute("aria-checked")).toBe("true");
    });
  });

  it("switches to Thread tab and displays linked items (work items, runs, issues, PRs)", () => {
    render(
      <ContextPane
        role={mockRole}
        threadContext={mockThreadContext}
        onToggleSchedule={vi.fn()}
      />
    );

    const threadTab = screen.getByRole("tab", { name: /thread/i });
    fireEvent.click(threadTab);

    expect(screen.getByText("Review runner lease")).not.toBeNull();
    expect(screen.getByText("#1322")).not.toBeNull();
    expect(screen.getByText("PR #1410")).not.toBeNull();
    expect(screen.getByText("CR-98")).not.toBeNull();
  });
});
