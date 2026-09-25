// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RunCard } from "../RunCard";
import type { RunCardData } from "../cardTypes";

describe("RunCard", () => {
  const MOCK_RUN: RunCardData = {
    id: "run-abc-789",
    run_number: 1420,
    status: "running",
    node: "DeskComp",
    provider: "claude-3-7-sonnet",
    elapsed_seconds: 42,
    logs_tail: [
      "[INFO] Checking out branch feat/1319",
      "[INFO] Running test suite...",
      "[SUCCESS] 24 tests passed",
    ],
    pr_number: 1413,
    pr_url: "https://github.com/D-sorganization/Runner_Dashboard/pull/1413",
    run_url: "/work/runs?run=run-abc-789",
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders live status, node, provider, and elapsed time", () => {
    render(<RunCard run={MOCK_RUN} />);

    expect(screen.getByText(/running/i)).toBeInTheDocument();
    expect(screen.getByText(/DeskComp/i)).toBeInTheDocument();
    expect(screen.getByText(/claude-3-7-sonnet/i)).toBeInTheDocument();
    expect(screen.getByText(/42s/i)).toBeInTheDocument();
  });

  it("renders deep links to run page and PR", () => {
    render(<RunCard run={MOCK_RUN} />);

    const runLink = screen.getByRole("link", { name: /view run/i });
    expect(runLink).toHaveAttribute("href", "/work/runs?run=run-abc-789");

    const prLink = screen.getByRole("link", { name: /pr #1413/i });
    expect(prLink).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/pull/1413"
    );
  });

  it("calls onCancel when Cancel button is clicked", () => {
    const handleCancel = vi.fn();
    render(
      <RunCard
        run={MOCK_RUN}
        onCancel={handleCancel}
      />
    );

    const cancelBtn = screen.getByRole("button", { name: /cancel run/i });
    fireEvent.click(cancelBtn);

    expect(handleCancel).toHaveBeenCalledTimes(1);
    expect(handleCancel).toHaveBeenCalledWith("run-abc-789");
  });

  it("toggles expandable log tail", () => {
    render(<RunCard run={MOCK_RUN} />);

    expect(screen.queryByText(/Checking out branch/i)).not.toBeInTheDocument();

    const logToggleBtn = screen.getByRole("button", { name: /logs/i });
    fireEvent.click(logToggleBtn);

    expect(screen.getByText(/Checking out branch/i)).toBeInTheDocument();
    expect(screen.getByText(/24 tests passed/i)).toBeInTheDocument();

    // Toggle off
    fireEvent.click(logToggleBtn);
    expect(screen.queryByText(/Checking out branch/i)).not.toBeInTheDocument();
  });
});
