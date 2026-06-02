// @vitest-environment jsdom
/**
 * Behaviour tests for pages/RemediationPRs.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836, pass 8).
 *
 * Covers:
 * 1. Loading state before the PRs fetch resolves.
 * 2. Renders a row per PR with repo / number / title / age.
 * 3. Repo + author + draft filters narrow the table.
 * 4. Selecting a row opens the bulk action bar; dispatch modal POSTs with the
 *    threaded principalName as approved_by.
 * 5. Fetch error renders the inline error banner (no crash — orthogonality).
 * 6. Empty list renders the "No open PRs found." placeholder.
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
import { RemediationPRsSubTab } from "../RemediationPRs";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const PRS = [
  {
    number: 11,
    repository: "org/alpha",
    title: "Fix flaky test",
    author: "octocat",
    age_hours: 5,
    draft: false,
    labels: ["bug", { name: "ci" }],
  },
  {
    number: 22,
    repository: "org/beta",
    title: "WIP refactor",
    user: { login: "botuser" },
    age_hours: 100,
    draft: true,
  },
];

function mockFetch(opts: { prs?: unknown; prsOk?: boolean; dispatchOk?: boolean }) {
  const fn = vi.fn((url: string) => {
    if (url.includes("/api/prs/dispatch")) {
      return Promise.resolve({
        ok: opts.dispatchOk !== false,
        status: opts.dispatchOk === false ? 500 : 200,
        json: () =>
          Promise.resolve(
            opts.dispatchOk === false ? { detail: "boom" } : { dispatched: 1 },
          ),
      } as Response);
    }
    // /api/prs
    return Promise.resolve({
      ok: opts.prsOk !== false,
      status: opts.prsOk === false ? 503 : 200,
      json: () => Promise.resolve(opts.prs ?? PRS),
    } as Response);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("RemediationPRsSubTab", () => {
  it("shows a loading indicator before the fetch resolves", async () => {
    let resolve!: (v: unknown) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise((r) => {
            resolve = r as (v: unknown) => void;
          }),
      ),
    );
    render(<RemediationPRsSubTab />);
    expect(screen.getByText("Loading PRs…")).toBeInTheDocument();
    resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    await waitFor(() =>
      expect(screen.getByText("No open PRs found.")).toBeInTheDocument(),
    );
  });

  it("renders a row per PR with key fields", async () => {
    mockFetch({});
    render(<RemediationPRsSubTab />);
    await waitFor(() =>
      expect(screen.getByText("Fix flaky test")).toBeInTheDocument(),
    );
    expect(screen.getByText("#11")).toBeInTheDocument();
    expect(screen.getByText("org/alpha")).toBeInTheDocument();
    expect(screen.getByText("octocat")).toBeInTheDocument();
    // 5h under 48h threshold
    expect(screen.getByText("5h")).toBeInTheDocument();
    // 100h -> 4d
    expect(screen.getByText("4d")).toBeInTheDocument();
    // "Draft" appears as both a column header and the per-row badge.
    expect(screen.getAllByText("Draft").length).toBeGreaterThanOrEqual(2);
  });

  it("filters by repo", async () => {
    mockFetch({});
    render(<RemediationPRsSubTab />);
    await waitFor(() =>
      expect(screen.getByText("Fix flaky test")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByPlaceholderText("Filter by repo (org/repo)…"), {
      target: { value: "alpha" },
    });
    expect(screen.getByText("Fix flaky test")).toBeInTheDocument();
    expect(screen.queryByText("WIP refactor")).not.toBeInTheDocument();
  });

  it("hides drafts when 'Show drafts' is unchecked", async () => {
    mockFetch({});
    render(<RemediationPRsSubTab />);
    await waitFor(() =>
      expect(screen.getByText("WIP refactor")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText("Show drafts"));
    expect(screen.queryByText("WIP refactor")).not.toBeInTheDocument();
    expect(screen.getByText("Fix flaky test")).toBeInTheDocument();
  });

  it("selects a row and dispatches with the principal as approved_by", async () => {
    const fetchFn = mockFetch({});
    render(<RemediationPRsSubTab principalName="dieter" />);
    await waitFor(() =>
      expect(screen.getByText("Fix flaky test")).toBeInTheDocument(),
    );
    // The header select-all checkbox carries title="Select all".
    fireEvent.click(screen.getByTitle("Select all"));
    expect(screen.getByText(/PR\(s\) selected/)).toBeInTheDocument();

    fireEvent.click(screen.getByText(/Dispatch to selected/));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Confirm dispatch"));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find((c) =>
        String(c[0]).includes("/api/prs/dispatch"),
      );
      expect(call).toBeTruthy();
      const body = JSON.parse((call![1] as RequestInit).body as string);
      expect(body.confirmation.approved_by).toBe("dieter");
      expect(body.provider).toBe("jules_api");
    });
  });

  it("renders the inline error banner when the fetch fails", async () => {
    mockFetch({ prsOk: false });
    render(<RemediationPRsSubTab />);
    await waitFor(() =>
      expect(screen.getByText(/Failed to load PRs/)).toBeInTheDocument(),
    );
  });

  it("renders the empty placeholder for an empty list", async () => {
    mockFetch({ prs: [] });
    render(<RemediationPRsSubTab />);
    await waitFor(() =>
      expect(screen.getByText("No open PRs found.")).toBeInTheDocument(),
    );
  });
});
