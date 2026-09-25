// @vitest-environment jsdom
/**
 * Behaviour tests for ProposalsPanel in Fleet Command (#1284, CR-7).
 *
 * Covers:
 * 1. Form validation: required fields enforced before submission.
 * 2. Successful creation: form submit sends POST /api/proposals and refreshes.
 * 3. List rendering: open proposals and decided proposals with outcome badges and meeting links.
 * 4. Duplicate candidate detection: 409 displays candidate list and confirm button.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProposalsPanel } from "../FleetCommand/ProposalsPanel";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const OPEN_PROPOSALS = {
  proposals: [
    {
      number: 101,
      title: "Add WebGPU Renderer",
      target_repos: ["Runner_Dashboard"],
      problem: "Canvas lags on 10k points.",
      evidence: "12fps benchmark.",
      options_considered: "SVG vs WebGPU",
      lean: "WebGPU",
      estimated_cost: "Medium",
      urgency: "Urgent",
      source: "operator",
      state: "open",
      decision_labels: [],
      created_at: "2026-09-24T12:00:00Z",
      updated_at: "2026-09-24T12:00:00Z",
    },
  ],
};

const DECIDED_PROPOSALS = {
  proposals: [
    {
      number: 99,
      title: "Self-Healing Runner Daemon",
      target_repos: ["Runner_Dashboard", "Repository_Management"],
      problem: "Runners hang on disk full.",
      evidence: "3 incidents last week.",
      options_considered: "Cron vs daemon",
      lean: "Daemon",
      estimated_cost: "Low",
      urgency: "Emergency",
      source: "agent-claude",
      state: "decided",
      decision: "accepted",
      decision_labels: ["board:accepted"],
      meeting_date: "2026-09-21",
      consensus_url: "/api/priorities/meetings/2026-09-21",
      created_at: "2026-09-18T10:00:00Z",
      updated_at: "2026-09-21T15:00:00Z",
    },
    {
      number: 95,
      title: "Migrate to Monorepo",
      target_repos: ["Runner_Dashboard"],
      problem: "Repo split friction.",
      evidence: "Multi-repo PR coordination.",
      options_considered: "Git submodules vs monorepo",
      lean: "Monorepo",
      estimated_cost: "High",
      urgency: "Routine",
      source: "human",
      state: "decided",
      decision: "declined",
      decision_labels: ["board:declined"],
      meeting_date: "2026-09-14",
      consensus_url: "/api/priorities/meetings/2026-09-14",
      created_at: "2026-09-10T10:00:00Z",
      updated_at: "2026-09-14T15:00:00Z",
    },
  ],
};

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

function stubFetch(customHandler?: (url: string, opts?: RequestInit) => { status: number; body: unknown } | undefined) {
  const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
    if (customHandler) {
      const custom = customHandler(url, opts);
      if (custom) return jsonResponse(custom.status, custom.body);
    }
    if (url.includes("/api/proposals?state=open") || url.endsWith("/api/proposals")) {
      return jsonResponse(200, OPEN_PROPOSALS);
    }
    if (url.includes("/api/proposals?state=decided")) {
      return jsonResponse(200, DECIDED_PROPOSALS);
    }
    return jsonResponse(404, { detail: "Not found" });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ProposalsPanel", () => {
  it("renders open and decided proposals with badges", async () => {
    stubFetch();
    render(<ProposalsPanel />);

    // Wait for open proposals to load
    await waitFor(() => {
      expect(screen.getByText("Add WebGPU Renderer")).toBeInTheDocument();
    });

    expect(screen.getByText("#101")).toBeInTheDocument();
    expect(screen.getAllByText("Runner_Dashboard").length).toBeGreaterThanOrEqual(1);

    // Decided proposals
    expect(screen.getByText("Self-Healing Runner Daemon")).toBeInTheDocument();
    expect(screen.getByText("accepted")).toBeInTheDocument();
    expect(screen.getByText("Meeting 2026-09-21")).toBeInTheDocument();

    expect(screen.getByText("Migrate to Monorepo")).toBeInTheDocument();
    expect(screen.getByText("declined")).toBeInTheDocument();
  });

  it("enforces form validation on required fields", async () => {
    const fetchMock = stubFetch();
    render(<ProposalsPanel />);

    await waitFor(() => {
      expect(screen.getByText("Add WebGPU Renderer")).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole("button", { name: /submit proposal/i });
    fireEvent.click(submitBtn);

    // Validation warning should appear and no POST should be made
    expect(screen.getByText(/please fill in all required fields/i)).toBeInTheDocument();
    const postCalls = fetchMock.mock.calls.filter(([, opts]) => (opts as RequestInit | undefined)?.method === "POST");
    expect(postCalls.length).toBe(0);
  });

  it("submits proposal when all required fields are filled", async () => {
    let postedPayload: any = null;
    stubFetch((url, opts) => {
      if (url === "/api/proposals" && opts?.method === "POST") {
        postedPayload = JSON.parse(opts.body as string);
        return {
          status: 201,
          body: {
            number: 102,
            ...postedPayload,
            state: "open",
            decision_labels: [],
            created_at: "2026-09-25T12:00:00Z",
            updated_at: "2026-09-25T12:00:00Z",
          },
        };
      }
      return undefined;
    });

    render(<ProposalsPanel />);

    await waitFor(() => {
      expect(screen.getByText("Add WebGPU Renderer")).toBeInTheDocument();
    });

    // Fill the form fields
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "New Suggestion" } });
    fireEvent.change(screen.getByLabelText(/target repo/i), { target: { value: "Runner_Dashboard" } });
    fireEvent.change(screen.getByLabelText(/problem/i), { target: { value: "A clear problem statement" } });
    fireEvent.change(screen.getByLabelText(/evidence/i), { target: { value: "Benchmark links and logs" } });
    fireEvent.change(screen.getByLabelText(/options considered/i), { target: { value: "Option 1, Option 2" } });
    fireEvent.change(screen.getByLabelText(/submitter'?s lean/i), { target: { value: "Lean towards Option 1" } });
    fireEvent.change(screen.getByLabelText(/estimated effort/i), { target: { value: "High" } });
    fireEvent.change(screen.getByLabelText(/urgency/i), { target: { value: "Emergency" } });

    const submitBtn = screen.getByRole("button", { name: /submit proposal/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(postedPayload).not.toBeNull();
    });

    expect(postedPayload.title).toBe("New Suggestion");
    expect(postedPayload.problem).toBe("A clear problem statement");
    expect(postedPayload.urgency).toBe("Emergency");
  });

  it("handles duplicate candidate response with confirmation", async () => {
    let confirmSent = false;
    stubFetch((url, opts) => {
      if (url === "/api/proposals" && opts?.method === "POST") {
        const body = JSON.parse(opts.body as string);
        if (!body.confirm_not_duplicate) {
          return {
            status: 409,
            body: {
              detail: {
                error: "duplicate_candidates",
                message: "Potential duplicate proposals found",
                candidates: [{ number: 90, title: "Existing Similar Proposal", url: "https://github.com/..." }],
              },
            },
          };
        }
        confirmSent = true;
        return {
          status: 201,
          body: { number: 103, ...body, state: "open", decision_labels: [] },
        };
      }
      return undefined;
    });

    render(<ProposalsPanel />);

    // Fill form
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "Existing Similar Proposal" } });
    fireEvent.change(screen.getByLabelText(/target repo/i), { target: { value: "Runner_Dashboard" } });
    fireEvent.change(screen.getByLabelText(/problem/i), { target: { value: "Problem statement" } });
    fireEvent.change(screen.getByLabelText(/evidence/i), { target: { value: "Evidence link" } });
    fireEvent.change(screen.getByLabelText(/options considered/i), { target: { value: "Options" } });
    fireEvent.change(screen.getByLabelText(/submitter'?s lean/i), { target: { value: "My lean" } });
    fireEvent.change(screen.getByLabelText(/estimated effort/i), { target: { value: "High" } });

    const submitBtn = screen.getByRole("button", { name: /submit proposal/i });
    fireEvent.click(submitBtn);

    // 409 should display candidate warning and confirmation button
    await waitFor(() => {
      expect(screen.getByText(/potential duplicate/i)).toBeInTheDocument();
      expect(screen.getByText(/Existing Similar Proposal/i)).toBeInTheDocument();
    });

    const confirmBtn = screen.getByRole("button", { name: /confirm not a duplicate/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(confirmSent).toBe(true);
    });
  });
});
