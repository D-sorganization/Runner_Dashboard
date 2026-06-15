// @vitest-environment jsdom
/**
 * Tests for Tests.tsx — decomposition #836 pass 3.
 *
 * Covers the extracted "Tests" tab behaviour:
 * 1. Smoke render.
 * 2. CI loading placeholder when no ciResults.
 * 3. Renders a CI row per result with a re-run button for failures.
 * 4. Re-run posts to /api/tests/rerun.
 * 5. Heavy-test repo cards render with dispatch controls.
 * 6. GitHub-actions dispatch posts to /api/heavy-tests/dispatch.
 * 7. Docker dispatch posts to /api/heavy-tests/docker.
 * 8. Recent-runs collapse renders when present.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TestsPage } from "../TestsPage";
import { TestsTab, type CiResult, type TestRepo } from "../Tests";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(payload: unknown) {
  return vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(payload),
    } as Response),
  );
}

const CI: CiResult[] = [
  {
    repo: "alpha",
    conclusion: "failure",
    head_branch: "main",
    run_number: 42,
    run_id: 100,
    updated_at: new Date().toISOString(),
    html_url: "https://example.com/alpha/42",
  },
  {
    repo: "beta",
    conclusion: "success",
    head_branch: "dev",
    run_number: 7,
    run_id: 200,
    updated_at: new Date().toISOString(),
    html_url: "https://example.com/beta/7",
  },
];

const REPOS: TestRepo[] = [
  {
    name: "UpstreamDrift",
    description: "physics parity",
    default_python: "3.11",
    python_versions: ["3.11", "3.12"],
    recent_runs: [
      {
        id: 1,
        run_number: 10,
        conclusion: "success",
        head_branch: "main",
        triggering_actor: "ci",
        updated_at: new Date().toISOString(),
        html_url: "https://example.com/run/10",
      },
    ],
  },
];

describe("TestsTab", () => {
  it("TestsPage owns the heavy-test and CI result fetches", async () => {
    const fetchMock = vi.fn((url: string) => {
      const payload =
        url === "/api/heavy-tests/repos"
          ? { repos: REPOS }
          : url === "/api/tests/ci-results"
            ? { results: CI }
            : {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(payload),
      } as Response);
    });
    global.fetch = fetchMock;

    render(<TestsPage />);

    expect(await screen.findByText("alpha")).toBeInTheDocument();
    const card = document.querySelector(".test-card") as HTMLElement;
    expect(within(card).getByText("UpstreamDrift")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/heavy-tests/repos",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tests/ci-results",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("renders without throwing (smoke test)", () => {
    expect(() =>
      render(<TestsTab testRepos={[]} loading={false} />),
    ).not.toThrow();
  });

  it("shows the CI loading placeholder when there are no CI results", () => {
    render(<TestsTab testRepos={[]} loading={false} ciResults={[]} />);
    expect(screen.getByText(/Loading CI results/i)).toBeInTheDocument();
  });

  it("renders a CI row per result with a re-run for failures", () => {
    render(<TestsTab testRepos={[]} loading={false} ciResults={CI} />);
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
    expect(screen.getByText("failure")).toHaveAttribute(
      "data-touch-primitive",
      "Badge",
    );
    expect(screen.getByText("success")).toHaveAttribute(
      "data-touch-primitive",
      "Badge",
    );
    // alpha (failure) gets a re-run button; beta (success) gets View.
    expect(
      screen.getByRole("button", { name: /Re-run Failed/i }),
    ).toBeInTheDocument();
  });

  it("posts to /api/tests/rerun when Re-run Failed is clicked", async () => {
    const fetchMock = mockFetch({ status: "ok" });
    global.fetch = fetchMock;
    render(<TestsTab testRepos={[]} loading={false} ciResults={CI} />);
    fireEvent.click(screen.getByRole("button", { name: /Re-run Failed/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/tests/rerun",
        expect.anything(),
      );
    });
  });

  it("renders heavy-test repo cards with dispatch controls", () => {
    render(<TestsTab testRepos={REPOS} loading={false} ciResults={CI} />);
    // "UpstreamDrift" appears both as a stat value and the card title; scope to
    // the card title to keep the assertion unambiguous.
    const card = document.querySelector(".test-card") as HTMLElement;
    expect(within(card).getByText("UpstreamDrift")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Run via GitHub Actions/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Run in Docker/i }),
    ).toBeInTheDocument();
  });

  it("posts to /api/heavy-tests/dispatch for the GitHub Actions button", async () => {
    const fetchMock = mockFetch({ status: "dispatched" });
    global.fetch = fetchMock;
    render(<TestsTab testRepos={REPOS} loading={false} ciResults={CI} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Run via GitHub Actions/i }),
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/heavy-tests/dispatch",
        expect.anything(),
      );
    });
  });

  it("posts to /api/heavy-tests/docker for the Docker button", async () => {
    const fetchMock = mockFetch({ status: "completed" });
    global.fetch = fetchMock;
    render(<TestsTab testRepos={REPOS} loading={false} ciResults={CI} />);
    fireEvent.click(screen.getByRole("button", { name: /Run in Docker/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/heavy-tests/docker",
        expect.anything(),
      );
    });
  });

  it("renders the recent-runs collapse when present", () => {
    render(<TestsTab testRepos={REPOS} loading={false} ciResults={CI} />);
    expect(screen.getByText(/Recent Heavy Test Runs/i)).toBeInTheDocument();
    const badge = screen.getByText("1 runs");
    expect(badge).toBeInTheDocument();
  });

  it("renders the heavy-test headline stats", () => {
    render(<TestsTab testRepos={REPOS} loading={false} ciResults={CI} />);
    const statRow = document.querySelector(".stat-row") as HTMLElement;
    expect(within(statRow).getByText(/Heavy Test Repos/i)).toBeInTheDocument();
  });

  it("uses shared primitives and scoped tests tab classes", () => {
    render(<TestsTab testRepos={REPOS} loading={false} ciResults={CI} />);
    expect(
      document.querySelector(".tests-tab__ci-section"),
    ).toBeInTheDocument();
    expect(
      document.querySelector(".tests-tab__table-wrap"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Re-run Failed/i }),
    ).toHaveAttribute("data-touch-primitive", "TouchButton");
    expect(
      screen.getByRole("button", { name: /Run via GitHub Actions/i }),
    ).toHaveAttribute("data-touch-primitive", "TouchButton");
  });
});
