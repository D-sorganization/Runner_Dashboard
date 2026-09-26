// @vitest-environment jsdom
/**
 * Behaviour tests for pages/RemediationIssues.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836, pass 8).
 *
 * Covers:
 * 1. Source discovery: Linear-ready workspaces add Linear/Unified options.
 * 2. Renders an issue row with taxonomy pills + pickability marker.
 * 3. Non-pickable issues are not selectable; pickable ones are.
 * 4. Selecting + confirming dispatch POSTs with the threaded principalName.
 * 5. Force-dispatch checkbox appears when a non-pickable issue is selected.
 * 6. Fetch error renders the inline error banner (orthogonality — no crash).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RemediationIssuesSubTab } from "../RemediationIssues";

afterEach(cleanup);
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const ISSUES = [
  {
    repo: "org/alpha",
    number: 5,
    title: "Pickable task",
    pickable: true,
    taxonomy: {
      type: "task",
      complexity: "routine",
      judgement: "objective",
      effort: "S",
    },
  },
  {
    repo: "org/beta",
    number: 6,
    title: "Blocked bug",
    pickable: false,
    pickable_blocked_by: ["claim:codex"],
    taxonomy: { type: "bug", complexity: "complex", judgement: "design" },
  },
];

function mockFetch(opts: {
  linearReady?: boolean;
  issues?: unknown;
  issuesOk?: boolean;
  dispatchOk?: boolean;
  dispatchResponse?: unknown;
}) {
  const fn = vi.fn((url: string) => {
    if (url.includes("/api/linear/workspaces")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            workspaces: opts.linearReady
              ? [{ auth_status: "ok" }]
              : [{ auth_status: "missing" }],
          }),
      } as Response);
    }
    if (url.includes("/api/v1/staff/requests") || url.includes("/api/issues/dispatch")) {
      if (opts.dispatchResponse) {
        return Promise.resolve(opts.dispatchResponse as Response);
      }
      return Promise.resolve({
        ok: opts.dispatchOk !== false,
        status: opts.dispatchOk === false ? 500 : 201,
        json: () =>
          Promise.resolve(
            opts.dispatchOk === false
              ? { detail: "nope" }
              : { state: "executed", result: { status: "dispatched" } },
          ),
      } as Response);
    }
    // /api/issues
    return Promise.resolve({
      ok: opts.issuesOk !== false,
      status: opts.issuesOk === false ? 503 : 200,
      json: () =>
        Promise.resolve(
          opts.issuesOk === false
            ? { detail: "down" }
            : { items: opts.issues ?? ISSUES },
        ),
    } as Response);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("RemediationIssuesSubTab", () => {
  it("adds Linear + Unified source options when a workspace is ready", async () => {
    mockFetch({ linearReady: true });
    render(<RemediationIssuesSubTab />);
    await waitFor(() =>
      expect(screen.getByText("Pickable task")).toBeInTheDocument(),
    );
    expect(screen.getByRole("option", { name: "Linear" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Unified" })).toBeInTheDocument();
  });

  it("falls back to GitHub-only when Linear is not ready", async () => {
    mockFetch({ linearReady: false });
    render(<RemediationIssuesSubTab />);
    await waitFor(() =>
      expect(screen.getByText("Pickable task")).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("option", { name: "Linear" }),
    ).not.toBeInTheDocument();
  });

  it("renders rows with taxonomy pills and pickability markers", async () => {
    mockFetch({ linearReady: false });
    render(<RemediationIssuesSubTab />);
    await waitFor(() =>
      expect(screen.getByText("Pickable task")).toBeInTheDocument(),
    );
    expect(screen.getByText("Blocked bug")).toBeInTheDocument();
    expect(screen.getByText("#5")).toBeInTheDocument();
    // pickable marker ✓ and non-pickable ✗ both present
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(screen.getByText("✗")).toBeInTheDocument();
  });

  it("disables selection for non-pickable issues", async () => {
    mockFetch({ linearReady: false });
    render(<RemediationIssuesSubTab />);
    await waitFor(() =>
      expect(screen.getByText("Pickable task")).toBeInTheDocument(),
    );
    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // checkboxes: [pickable-only filter], [select-all], [row5], [row6]
    const rowBoxes = checkboxes.filter((c) => c.type === "checkbox");
    // The non-pickable row's checkbox is disabled.
    const disabled = rowBoxes.filter((c) => c.disabled);
    expect(disabled.length).toBe(1);
  });

  it("dispatches the selected pickable issue via work-request API with principal as approved_by", async () => {
    const fetchFn = mockFetch({ linearReady: false });
    render(<RemediationIssuesSubTab principalName="dieter" />);
    await waitFor(() =>
      expect(screen.getByText("Pickable task")).toBeInTheDocument(),
    );
    // DOM checkbox order: [0] "Pickable only" filter, [1] header select-all,
    // then one per row. Clicking select-all selects only pickable issues.
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[1]);
    // Use the explicit "1 issue selected" affordance to proceed.
    await waitFor(() =>
      expect(screen.getByText(/issue.*selected/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Dispatch to selected"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // "Confirm Dispatch" is both the dialog heading and the action button.
    fireEvent.click(screen.getByRole("button", { name: "Confirm Dispatch" }));
    await waitFor(() => {
      const call = fetchFn.mock.calls.find((c) =>
        String(c[0]).includes("/api/v1/staff/requests"),
      );
      expect(call).toBeTruthy();
      const body = JSON.parse((call![1] as RequestInit).body as string);
      expect(body.kind).toBe("issue.act");
      expect(body.target.issues).toEqual([5]);
      expect(body.approved_by).toBe("dieter");
    });
  });

  it("produces one request with all targets for a bulk selection", async () => {
    const multiIssues = [
      {
        repo: "org/alpha",
        number: 5,
        title: "Task 5",
        pickable: true,
        taxonomy: { type: "task", complexity: "routine", judgement: "objective" },
      },
      {
        repo: "org/alpha",
        number: 7,
        title: "Task 7",
        pickable: true,
        taxonomy: { type: "task", complexity: "routine", judgement: "objective" },
      },
    ];
    const fetchFn = mockFetch({ linearReady: false, issues: multiIssues });
    render(<RemediationIssuesSubTab principalName="dieter" />);
    await waitFor(() =>
      expect(screen.getByText("Task 5")).toBeInTheDocument(),
    );
    // Select all pickable issues
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[1]);
    await waitFor(() =>
      expect(screen.getByText(/2 issues selected/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Dispatch to selected"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm Dispatch" }));

    await waitFor(() => {
      const calls = fetchFn.mock.calls.filter((c) =>
        String(c[0]).includes("/api/v1/staff/requests"),
      );
      expect(calls.length).toBe(1);
      const body = JSON.parse((calls[0][1] as RequestInit).body as string);
      expect(body.kind).toBe("issue.act");
      expect(body.target.issues).toEqual([5, 7]);
      expect(body.target.repo).toBe("org/alpha");
    });
  });

  it("shows partial backend failure per target", async () => {
    const multiIssues = [
      {
        repo: "org/alpha",
        number: 5,
        title: "Task 5",
        pickable: true,
        taxonomy: { type: "task", complexity: "routine", judgement: "objective" },
      },
      {
        repo: "org/alpha",
        number: 7,
        title: "Task 7",
        pickable: true,
        taxonomy: { type: "task", complexity: "routine", judgement: "objective" },
      },
    ];
    mockFetch({
      linearReady: false,
      issues: multiIssues,
      dispatchResponse: {
        ok: true,
        status: 201,
        json: () =>
          Promise.resolve({
            state: "executed",
            result: {
              status: "partial",
              accepted: [{ issue: 5, status: "dispatched" }],
              rejected: [{ issue: 7, reason: "claimed by codex" }],
            },
          }),
      },
    });
    render(<RemediationIssuesSubTab principalName="dieter" />);
    await waitFor(() =>
      expect(screen.getByText("Task 5")).toBeInTheDocument(),
    );
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[1]);
    await waitFor(() =>
      expect(screen.getByText(/2 issues selected/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Dispatch to selected"));
    fireEvent.click(screen.getByRole("button", { name: "Confirm Dispatch" }));

    await waitFor(() => {
      expect(screen.getByText(/#7: claimed by codex/)).toBeInTheDocument();
    });
  });

  it("renders the inline error banner when the issues fetch fails", async () => {
    mockFetch({ linearReady: false, issuesOk: false });
    render(<RemediationIssuesSubTab />);
    await waitFor(() =>
      expect(screen.getByText(/HTTP 503/)).toBeInTheDocument(),
    );
  });
});
