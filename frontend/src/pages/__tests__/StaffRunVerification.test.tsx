/**
 * Post-run verification on the run detail page (WP-1.1, #1516): the verdict, its
 * reason and the PR it checked are shown next to the run status.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "../Staff/RunDetail";

const RUN = {
  id: "run-v",
  role: "night-watch",
  provider: "claude",
  model: null,
  machine: "DeskComputer",
  repo: "UpstreamDrift",
  target_kind: "issue",
  target_ref: "10622",
  prompt: "Fix the thing",
  status: "succeeded",
  created_at: "2026-09-25T10:00:00Z",
  ended_at: "2026-09-25T10:30:00Z",
  cost_usd: 0.4,
  input_tokens: 10,
  output_tokens: 20,
  branch: "staff/night-watch-10622-run-v",
};

function stubRun(run: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ run, events: [], ...extra }),
      }),
    ),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RunDetail post-run verification (#1516)", () => {
  it("shows the verdict, its reason and a link to the checked PR", async () => {
    stubRun({
      ...RUN,
      verification: "failed",
      verification_detail: "PR #8120 head CI is failing",
      pr_number: 8120,
    });
    render(<RunDetail runId="run-v" onBack={() => {}} />);

    const verdict = await screen.findByTestId("run-verification");
    expect(verdict).toHaveTextContent("failed");
    expect(verdict).toHaveTextContent("PR #8120 head CI is failing");
    expect(screen.getByRole("link", { name: "#8120" })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/UpstreamDrift/pull/8120",
    );
  });

  it("omits the row for a run that was never checked", async () => {
    stubRun(RUN);
    render(<RunDetail runId="run-v" onBack={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.queryByTestId("run-verification")).not.toBeInTheDocument();
  });

  it("renders evidence and diff links from existing run detail fields (#3176)", async () => {
    stubRun(
      {
        ...RUN,
        artifact: "pr_open",
        branch: "staff/night-watch-10622-run-v",
        remote_session_url: "https://claude.ai/chat/demo-session",
      },
      { logs_ref: "/api/v1/diagnostics/logs?run_id=run-v" },
    );
    render(<RunDetail runId="run-v" onBack={() => {}} />);

    const section = await screen.findByTestId("run-evidence-links");
    expect(section).toBeInTheDocument();
    expect(screen.getByTestId("run-evidence-artifact")).toHaveTextContent("PR opened");
    expect(screen.getByRole("link", { name: "Issue #10622" })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/UpstreamDrift/issues/10622",
    );
    expect(screen.getByRole("link", { name: /Diff \(staff\/night-watch/ })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/UpstreamDrift/compare/main...staff/night-watch-10622-run-v",
    );
    expect(screen.getByRole("link", { name: "Review session" })).toHaveAttribute(
      "href",
      "https://claude.ai/chat/demo-session",
    );
    expect(screen.getByRole("link", { name: "Diagnostic logs" })).toHaveAttribute(
      "href",
      "/api/v1/diagnostics/logs?run_id=run-v",
    );
  });
});
