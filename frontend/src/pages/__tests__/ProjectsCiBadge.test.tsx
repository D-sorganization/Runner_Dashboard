// @vitest-environment jsdom
/**
 * Projects cards carry a per-repo CI-status badge (#1338, SC-G6: the one thing
 * the retired Organization tab had that Projects lacked). CI data comes from
 * GET /api/repos; a failure there never hides the cards and is shown, not
 * swallowed.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsPage } from "../ProjectsPage";
import type { ProjectOverview, ProjectsResponse } from "../Projects";

const BASE: Omit<ProjectOverview, "repo"> = {
  charter_present: false,
  features: [],
  progress: { planned: 0, in_progress: 0, shipped: 0, parked: 0, percent_shipped: 0 },
  status_present: false,
  decisions_needed: [],
  last_steward_run: null,
};

const PROJECTS: ProjectsResponse = {
  projects: ["Green", "Red", "Busy", "Quiet", "Unlisted"].map((repo) => ({ ...BASE, repo })),
  count: 5,
  cache_ttl_seconds: 600,
};

const REPOS = {
  repos: [
    {
      name: "Green",
      last_ci_status: "completed",
      last_ci_conclusion: "success",
      last_ci_run_url: "https://github.com/example/Green/actions/runs/1",
    },
    {
      name: "Red",
      last_ci_status: "completed",
      last_ci_conclusion: "failure",
      last_ci_run_url: "https://github.com/example/Red/actions/runs/2",
    },
    { name: "Busy", last_ci_status: "in_progress", last_ci_conclusion: null },
    { name: "Quiet", last_ci_status: null, last_ci_conclusion: null },
  ],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

function card(repo: string) {
  return within(screen.getByTestId(`project-card-${repo}`));
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Projects CI-status badge (#1338)", () => {
  it("shows each repo's latest CI result, linked to the run", async () => {
    global.fetch = vi.fn((url: string) =>
      Promise.resolve(jsonResponse(url === "/api/repos" ? REPOS : PROJECTS)),
    ) as unknown as typeof fetch;

    render(<ProjectsPage />);

    await waitFor(() => expect(card("Green").getByText("CI success")).toBeInTheDocument());
    expect(card("Green").getByRole("link", { name: "CI success" })).toHaveAttribute(
      "href",
      "https://github.com/example/Green/actions/runs/1",
    );
    expect(card("Red").getByRole("link", { name: "CI failure" })).toHaveAttribute(
      "href",
      "https://github.com/example/Red/actions/runs/2",
    );
    expect(card("Busy").getByText("CI in_progress")).toBeInTheDocument();
    expect(card("Quiet").getByText("No CI")).toBeInTheDocument();
    expect(card("Unlisted").getByText("CI unknown")).toBeInTheDocument();
  });

  it("keeps the cards and says so when the CI source fails", async () => {
    global.fetch = vi.fn((url: string) =>
      url === "/api/repos"
        ? Promise.resolve(jsonResponse({ detail: "boom" }, 502))
        : Promise.resolve(jsonResponse(PROJECTS)),
    ) as unknown as typeof fetch;

    render(<ProjectsPage />);

    await waitFor(() =>
      expect(screen.getByText(/CI status unavailable/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/CI status unavailable/i)).toHaveTextContent("502");
    expect(card("Green").getByText("CI unknown")).toBeInTheDocument();
  });
});
