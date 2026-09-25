/**
 * RunCard.test.tsx — Unit tests for RunCard (SC-D5, Issue #1319).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { RunCard } from "../cards/RunCard";
import type { RunCardData } from "../cards/cardTypes";

describe("RunCard", () => {
  const activeRun: RunCardData = {
    run_id: "run-abc-987",
    status: "running",
    node: "oglaptop-linux",
    provider: "anthropic",
    elapsed_seconds: 142,
    log_tail: ["Step 1: cloning repo...", "Step 2: running test suite...", "Step 3: coverage 98%"],
    pr: 1412,
    pr_url: "https://github.com/D-sorganization/Runner_Dashboard/pull/1412",
    run_url: "/runs/run-abc-987",
    can_cancel: true,
  };

  it("renders status, node, provider, elapsed time and links", () => {
    render(<RunCard run={activeRun} />);
    expect(screen.getByText("run-abc-987")).toBeInTheDocument();
    expect(screen.getByText(/running/i)).toBeInTheDocument();
    expect(screen.getByText(/oglaptop-linux/)).toBeInTheDocument();
    expect(screen.getByText(/anthropic/)).toBeInTheDocument();
    expect(screen.getByText(/2m 22s/)).toBeInTheDocument();

    const prLink = screen.getByRole("link", { name: /#1412/i });
    expect(prLink).toHaveAttribute("href", "https://github.com/D-sorganization/Runner_Dashboard/pull/1412");
  });

  it("expandable log tail expands and shows log entries", () => {
    render(<RunCard run={activeRun} />);
    const logToggle = screen.getByTestId("log-toggle-btn");
    expect(screen.queryByText(/Step 1: cloning repo/)).toBeNull();

    fireEvent.click(logToggle);
    expect(screen.getByText(/Step 1: cloning repo/)).toBeInTheDocument();
    expect(screen.getByText(/Step 3: coverage 98%/)).toBeInTheDocument();

    fireEvent.click(logToggle);
    expect(screen.queryByText(/Step 1: cloning repo/)).toBeNull();
  });

  it("cancel button triggers onCancelRun once with double-click guard", () => {
    const onCancel = vi.fn().mockImplementation(() => new Promise((r) => setTimeout(r, 100)));
    render(<RunCard run={activeRun} onCancelRun={onCancel} />);

    const cancelBtn = screen.getByTestId("cancel-run-btn");
    fireEvent.click(cancelBtn);
    fireEvent.click(cancelBtn);

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledWith("run-abc-987");
  });

  it("cancel button is not shown when run is terminal", () => {
    const completedRun: RunCardData = {
      ...activeRun,
      status: "completed",
      can_cancel: false,
    };
    render(<RunCard run={completedRun} />);
    expect(screen.queryByTestId("cancel-run-btn")).toBeNull();
  });
});
