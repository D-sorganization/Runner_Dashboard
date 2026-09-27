// @vitest-environment jsdom
/**
 * Projects cards carry each repo's assessment score history and an
 * "Request assessment" action (#1338, SC-G6: the retired Assessments tab is
 * split). The request goes through the Staff request API as the existing
 * `assessment.run` kind (Jules-Assess-Repo.yml), with no staff role.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  projects: ["Alpha", "Beta"].map((repo) => ({ ...BASE, repo })),
  count: 2,
  cache_ttl_seconds: 600,
};

const SCORES = {
  scores: [
    { repo: "Alpha", score: 0.72, date: "2026-09-01T00:00:00Z", provider: "jules_api", summary: "older run" },
    { repo: "Alpha", score: 8.5, date: "2026-09-20T00:00:00Z", provider: "codex", summary: "newer run" },
    { repo: "Gamma", score: 5, date: "2026-09-02", provider: "claude", summary: "not a project" },
  ],
};

interface Call {
  url: string;
  init?: RequestInit;
}

let calls: Call[] = [];

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(payload),
    text: () => Promise.resolve(JSON.stringify(payload)),
  } as Response;
}

function mockFetch(scores: { status: number; body: unknown } = { status: 200, body: SCORES }) {
  calls = [];
  global.fetch = vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/api/assessments/scores") {
      return Promise.resolve(jsonResponse(scores.body, scores.status));
    }
    if (url === "/api/repos") return Promise.resolve(jsonResponse({ repos: [] }));
    if (url === "/api/v1/staff/requests") {
      return Promise.resolve(jsonResponse({ request_id: "r1", run_id: "abcdef123456", result: { status: "queued" } }));
    }
    return Promise.resolve(jsonResponse(PROJECTS));
  }) as unknown as typeof fetch;
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

describe("Projects assessments (#1338)", () => {
  it("shows each repo's score history on its card, newest first", async () => {
    mockFetch();
    render(<ProjectsPage />);

    await waitFor(() => expect(card("Alpha").getByText("newer run")).toBeInTheDocument());
    const summaries = card("Alpha")
      .getAllByTestId("assessment-summary")
      .map((el) => el.textContent);
    expect(summaries).toEqual(["newer run", "older run"]);
    expect(card("Alpha").getAllByText("8.5")).toHaveLength(2); // latest in the summary + history row
    expect(card("Alpha").getByText("72%")).toBeInTheDocument();
    expect(card("Alpha").getByText(/Assessments \(2\)/)).toBeInTheDocument();
    expect(card("Beta").getByText(/No assessment recorded/i)).toBeInTheDocument();
    expect(screen.queryByText("not a project")).not.toBeInTheDocument();
  });

  it("requests an assessment as the existing assessment.run kind, with no role", async () => {
    mockFetch();
    render(<ProjectsPage />);

    const button = await card("Beta").findByRole("button", { name: "Request assessment for Beta" });
    fireEvent.click(button);

    await waitFor(() => expect(card("Beta").getByText(/Assessment requested/i)).toBeInTheDocument());
    const post = calls.find((c) => c.url === "/api/v1/staff/requests");
    expect(post).toBeDefined();
    const body = JSON.parse(String(post?.init?.body));
    expect(body.kind).toBe("assessment.run");
    expect(body.target).toEqual({ repo: "Beta", ref: "" });
    expect(body).not.toHaveProperty("role");
    expect(body.dry_run).toBe(false);
  });

  it("keeps the cards and says so when the score source fails", async () => {
    mockFetch({ status: 503, body: { detail: "down" } });
    render(<ProjectsPage />);

    await waitFor(() =>
      expect(screen.getByText(/Assessment scores unavailable/i)).toHaveTextContent("503"),
    );
    expect(screen.getByTestId("project-card-Alpha")).toBeInTheDocument();
  });
});
