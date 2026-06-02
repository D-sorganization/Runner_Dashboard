// @vitest-environment jsdom
/**
 * Tests for Org.tsx — decomposition #836 pass 3.
 *
 * Covers the extracted "Organization" tab behaviour:
 * 1. Smoke render.
 * 2. Renders a row per repo with name + CI badge.
 * 3. Headline stats aggregate PRs/issues across repos.
 * 4. Search narrows the visible rows.
 * 5. Empty state when no repos (and a loading variant).
 * 6. Sort buttons reflect aria-pressed state.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { OrgTab, type OrgRepo } from "../Org";

afterEach(cleanup);

const REPOS: OrgRepo[] = [
  {
    name: "alpha",
    description: "first repo",
    language: "TypeScript",
    url: "https://example.com/alpha",
    private: false,
    open_prs: 3,
    open_issues: 2,
    updated_at: new Date().toISOString(),
    last_ci_status: "completed",
    last_ci_conclusion: "success",
    last_ci_run_url: "https://example.com/alpha/ci",
  },
  {
    name: "beta",
    description: "second repo",
    language: "Python",
    url: "https://example.com/beta",
    private: true,
    open_prs: 1,
    open_issues: 5,
    updated_at: new Date(Date.now() - 86400000).toISOString(),
  },
];

describe("OrgTab", () => {
  it("renders without throwing (smoke test)", () => {
    expect(() => render(<OrgTab repos={[]} loading={false} />)).not.toThrow();
  });

  it("renders a row per repo", () => {
    render(<OrgTab repos={REPOS} loading={false} />);
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
    // alpha has a CI conclusion badge.
    expect(screen.getByText("success")).toBeInTheDocument();
    // beta has no CI.
    expect(screen.getByText("No CI")).toBeInTheDocument();
  });

  it("aggregates PRs and issues across repos in the headline stats", () => {
    render(<OrgTab repos={REPOS} loading={false} />);
    const statRow = document.querySelector(".stat-row") as HTMLElement;
    // Total open PRs = 3 + 1 = 4.
    expect(within(statRow).getByText("4")).toBeInTheDocument();
    // Total open issues = 2 + 5 = 7.
    expect(within(statRow).getByText("7")).toBeInTheDocument();
  });

  it("filters rows by the search box", () => {
    render(<OrgTab repos={REPOS} loading={false} />);
    fireEvent.change(screen.getByLabelText(/Search repositories/i), {
      target: { value: "alpha" },
    });
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.queryByText("beta")).not.toBeInTheDocument();
  });

  it("shows the empty state when no repos and not loading", () => {
    render(<OrgTab repos={[]} loading={false} />);
    expect(screen.getByText("No repos found")).toBeInTheDocument();
  });

  it("shows the loading placeholder when empty and loading", () => {
    render(<OrgTab repos={[]} loading={true} />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("marks the active sort button via aria-pressed", () => {
    render(<OrgTab repos={REPOS} loading={false} />);
    const recent = screen.getByRole("button", { name: "Recent" });
    expect(recent).toHaveAttribute("aria-pressed", "true");
    const prs = screen.getByRole("button", { name: "PRs" });
    fireEvent.click(prs);
    expect(prs).toHaveAttribute("aria-pressed", "true");
    expect(recent).toHaveAttribute("aria-pressed", "false");
  });

  it("prefers org_open_issues from stats when provided", () => {
    render(<OrgTab repos={REPOS} loading={false} stats={{ org_open_issues: 99 }} />);
    const statRow = document.querySelector(".stat-row") as HTMLElement;
    expect(within(statRow).getByText("99")).toBeInTheDocument();
  });
});
