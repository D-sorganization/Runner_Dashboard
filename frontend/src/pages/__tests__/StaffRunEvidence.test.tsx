/**
 * Run detail evidence and diff links (#3176): citations from the references API
 * and GitHub diff/compare links derived from the run record.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "../Staff/RunDetail";

const RUN = {
  id: "run-evidence",
  role: "cartographer",
  provider: "claude",
  model: null,
  machine: "DeskComputer",
  repo: "Runner_Dashboard",
  target_kind: "issue",
  target_ref: "3176",
  prompt: "Spike task-detail links",
  status: "succeeded",
  created_at: "2026-10-27T10:00:00Z",
  ended_at: "2026-10-27T10:30:00Z",
  cost_usd: 0.2,
  input_tokens: 10,
  output_tokens: 20,
  branch: "cursor/task-detail-evidence-links-141f",
  pr_number: 99,
};

const CODEBASE_REF = {
  kind: "codebase",
  node: "DeskComputer",
  repo: "Runner_Dashboard",
  commit_sha: "c".repeat(40),
  relative_path: "frontend/src/pages/Staff/RunDetail.tsx",
  line_start: 1,
  line_end: 3,
  content_hash: "d".repeat(64),
  citation: "[ref:codebase]",
};

function stubFetch(run: Record<string, unknown>, references: unknown[] = [CODEBASE_REF]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/references")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ run_id: run.id, references }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ run, events: [] }),
      });
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RunDetail evidence and diff links (#3176)", () => {
  it("shows review evidence citations and GitHub diff links", async () => {
    stubFetch(RUN);
    render(<RunDetail runId="run-evidence" onBack={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("run-evidence")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /RunDetail\.tsx/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/blob/"),
    );

    const diff = screen.getByTestId("run-diff-links");
    expect(diff).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /PR #99 files/i })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/pull/99/files",
    );
    expect(screen.getByRole("link", { name: /Compare branch/i })).toHaveAttribute(
      "href",
      expect.stringContaining("compare/main...cursor"),
    );
    expect(screen.getByRole("link", { name: /Issue #3176/i })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/issues/3176",
    );
  });

  it("omits evidence and diff sections when there is nothing to link", async () => {
    stubFetch({ ...RUN, branch: "", pr_number: null, target_kind: "prompt", target_ref: "" }, []);
    render(<RunDetail runId="run-evidence" onBack={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.queryByTestId("run-evidence")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-diff-links")).not.toBeInTheDocument();
  });
});
