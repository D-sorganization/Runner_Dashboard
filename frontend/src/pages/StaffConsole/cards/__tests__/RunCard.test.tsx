// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  // #1547: run cards follow the run, answer questions and cancel.

  const ASKING: RunCardData = {
    id: "run-ask-1",
    status: "needs_input",
    node: "DeskComp",
    provider: "claude",
    question: "Which repository should I look at?",
  };

  it("shows a needs-input question and sends the typed answer", async () => {
    const onAnswer = vi.fn().mockResolvedValue(true);
    render(<RunCard run={ASKING} onAnswer={onAnswer} />);

    expect(screen.getByText("Which repository should I look at?")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: /answer/i }), { target: { value: "Runner_Dashboard" } });
    fireEvent.click(screen.getByRole("button", { name: /send answer/i }));

    expect(onAnswer).toHaveBeenCalledWith("run-ask-1", "Runner_Dashboard");
    await waitFor(() => expect(screen.getByText(/answer sent/i)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /send answer/i })).not.toBeInTheDocument();
  });

  it("keeps the answer when it is refused", async () => {
    const onAnswer = vi.fn().mockResolvedValue(false);
    render(<RunCard run={ASKING} onAnswer={onAnswer} />);

    fireEvent.change(screen.getByRole("textbox", { name: /answer/i }), { target: { value: "Runner_Dashboard" } });
    fireEvent.click(screen.getByRole("button", { name: /send answer/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /send answer/i })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: /answer/i })).toHaveValue("Runner_Dashboard");
  });

  it("does not send an empty answer", () => {
    render(<RunCard run={ASKING} onAnswer={vi.fn()} />);

    expect(screen.getByRole("button", { name: /send answer/i })).toBeDisabled();
  });

  it("shows who answered and the continuation instead of the form", () => {
    render(
      <RunCard
        run={{ ...ASKING, answered_by: "human:e2e-operator", continued_by: "run-ask-2" }}
        onAnswer={vi.fn()}
      />,
    );

    expect(screen.getByText(/answered by human:e2e-operator/i)).toBeInTheDocument();
    expect(screen.getByText(/run-ask-2/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /answer/i })).not.toBeInTheDocument();
  });

  it("shows the result of a completed run and the error of a failed one", () => {
    const { rerender } = render(<RunCard run={{ id: "r1", status: "completed", summary: "fake run finished" }} />);
    expect(screen.getByText(/fake run finished/)).toBeInTheDocument();

    rerender(<RunCard run={{ id: "r1", status: "failed", error: "provider crashed" }} />);
    expect(screen.getByText(/provider crashed/)).toBeInTheDocument();
  });

  it("re-enables Cancel when the cancel is refused", async () => {
    const onCancel = vi.fn().mockResolvedValue(false);
    render(<RunCard run={MOCK_RUN} onCancel={onCancel} />);

    fireEvent.click(screen.getByRole("button", { name: /cancel run/i }));

    expect(screen.getByRole("button", { name: /cancel run/i })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: /cancel run/i })).toBeEnabled());
  });
});
