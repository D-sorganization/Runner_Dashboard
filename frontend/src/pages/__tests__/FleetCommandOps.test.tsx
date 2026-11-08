// @vitest-environment jsdom
/**
 * Behaviour tests for pages/FleetCommand operations — Proposals, Claims, Dispatch.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetCommandPage } from "../FleetCommand";
import { operatorSession } from "../FleetCommand/fleetApi";
import {
  headerOf,
  openSection,
  stubFetch,
  writes,
} from "./fleetCommandTestHelpers";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("FleetCommandPage — proposals", () => {
  it("renders decided proposals for the meeting date", async () => {
    stubFetch((url) => {
      if (url.includes("/api/proposals?state=decided")) {
        return {
          status: 200,
          body: {
            proposals: [
              {
                number: 42,
                title: "Decided in this meeting",
                target_repos: ["Runner_Dashboard"],
                decision: "accepted",
                meeting_date: "2026-09-21",
                state: "decided",
              },
            ],
          },
        };
      }
      return undefined;
    });
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByTestId("priorities-decided-proposals")).toBeInTheDocument());
    expect(screen.getByText("Decided in this meeting")).toBeInTheDocument();
    expect(screen.getByText("#42")).toBeInTheDocument();
  });

  it("switches to Proposals tab and renders proposal form and lists", async () => {
    stubFetch((url) => {
      if (url.includes("/api/proposals")) {
        return {
          status: 200,
          body: { proposals: [] },
        };
      }
      return undefined;
    });
    render(<FleetCommandPage />);
    const proposalsTab = screen.getByRole("tab", { name: /proposals/i });
    fireEvent.click(proposalsTab);
    await waitFor(() => expect(screen.getByTestId("proposal-form-frame")).toBeInTheDocument());
    expect(screen.getByTestId("open-proposals-frame")).toBeInTheDocument();
    expect(screen.getByTestId("decided-proposals-frame")).toBeInTheDocument();
  });

  it("pre-fills proposal form from URL search params", async () => {
    const origLocation = window.location;
    delete (window as unknown as { location?: Location }).location;
    window.location = {
      ...origLocation,
      search: "?section=proposals&title=Prefilled+Title&repo=Runner_Dashboard&problem=Big+problem",
    };
    try {
      stubFetch((url) => {
        if (url.includes("/api/proposals")) {
          return { status: 200, body: { proposals: [] } };
        }
        return undefined;
      });
      render(<FleetCommandPage />);
      await waitFor(() => expect(screen.getByTestId("proposal-form-frame")).toBeInTheDocument());
      expect(screen.getByLabelText("Title")).toHaveValue("Prefilled Title");
      expect(screen.getByLabelText("Target Repo(s)")).toHaveValue("Runner_Dashboard");
      expect(screen.getByLabelText("Problem Statement")).toHaveValue("Big problem");
    } finally {
      window.location = origLocation;
    }
  });
});

describe("FleetCommandPage — claims", () => {
  it("checks a claim, surfaces a 409 holder and releases", async () => {
    const fetchMock = stubFetch((url, opts) => {
      if (url === "/api/coordination/claims?repo=Tools&issue=42") {
        return {
          status: 200,
          body: {
            available: true,
            held: true,
            agent: "codex",
            reason: "lease active",
            expires_at: "2099-01-01T00:00:00Z",
          },
        };
      }
      if (url === "/api/coordination/claims" && opts?.method === "POST") {
        return {
          status: 409,
          body: { detail: { error: "issue is claimed", held_by: "codex", guidance: "pick another issue" } },
        };
      }
      if (url === "/api/coordination/claims/release") return { status: 200, body: { ok: true } };
      return undefined;
    });
    render(<FleetCommandPage />);
    openSection("Claims");
    fireEvent.change(screen.getByRole("textbox", { name: "Claim repo" }), { target: { value: "Tools" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Claim issue" }), { target: { value: "#42" } });
    fireEvent.click(screen.getByRole("button", { name: "Check claim" }));
    await waitFor(() => expect(screen.getByTestId("claim-status")).toHaveTextContent("claimed by codex"));

    fireEvent.click(screen.getByRole("button", { name: /Claim maintenance session/i }));
    await waitFor(() =>
      expect(screen.getByTestId("claim-error")).toHaveTextContent(
        "issue is claimed — held by codex — pick another issue",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /Release maintenance session/i }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Released Tools#42."));
    const release = writes(fetchMock, "POST").find(([u]) => u === "/api/coordination/claims/release");
    expect(release).toBeDefined();
    expect(headerOf(release![1], "X-Requested-With")).toBe("XMLHttpRequest");
    expect(JSON.parse(String(release![1].body))).toEqual({
      repo: "Tools",
      issue: 42,
      session: operatorSession(),
      reason: "work completed",
    });
  });
});

describe("FleetCommandPage — dispatch", () => {
  it("previews with dry_run then dispatches and links to the Staff tab run", async () => {
    const fetchMock = stubFetch((url, opts) => {
      if (url !== "/api/v1/staff/requests") return undefined;
      const body = JSON.parse(String(opts?.body));
      return body.dry_run
        ? {
            status: 200,
            body: {
              state: "planned",
              plan: {
                dry_run: true,
                machine: "local",
                plan: {
                  role: "night-watch",
                  provider: "claude",
                  model: null,
                  repo: "Tools",
                  target_kind: "issue",
                  target_ref: "42",
                  prompt: "p",
                  argv: ["claude"],
                  branch: "staff/nw-42",
                  lease_ritual: true,
                },
              },
            },
          }
        : { status: 201, body: { state: "executed", run_id: "run-77" } };
    });
    render(<FleetCommandPage />);
    openSection("Dispatch");
    await waitFor(() => expect(screen.getByLabelText("Role")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Repo"), { target: { value: "Tools" } });
    fireEvent.change(screen.getByLabelText("Issue #"), { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(screen.getByTestId("assign-plan")).toBeInTheDocument());
    expect(screen.getByTestId("plan-branch")).toHaveTextContent("staff/nw-42");

    fireEvent.click(screen.getByRole("button", { name: "Dispatch" }));
    await waitFor(() => expect(screen.getByTestId("dispatch-run-link")).toHaveAttribute("href", "/?run=run-77"));
    const posts = writes(fetchMock, "POST").map(([, o]) => JSON.parse(String(o.body)).dry_run);
    expect(posts).toEqual([true, false]);
  });

  it("shows 'not available' when the Staff Hub is absent", async () => {
    stubFetch((url) => (url === "/api/staff/roster" || url === "/api/v1/staff/roster" ? { status: 404, body: { detail: "Not Found" } } : undefined));
    render(<FleetCommandPage />);
    openSection("Dispatch");
    await waitFor(() => expect(screen.getByTestId("fleet-dispatch-unavailable")).toBeInTheDocument());
  });
});

describe("FleetCommandPage — job board", () => {
  const JOBS = {
    available: true,
    complete: true,
    jobs: [
      {
        repo: "Tools",
        issue: 10,
        title: "High tools work",
        url: "https://github.com/D-sorganization/Tools/issues/10",
        labels: ["high"],
        priority: "high",
        updated_at: "2026-11-01T12:00:00Z",
      },
      {
        repo: "Tools",
        issue: 11,
        title: "Low tools work",
        url: "https://github.com/D-sorganization/Tools/issues/11",
        labels: ["low"],
        priority: "low",
        updated_at: "2026-10-15T12:00:00Z",
      },
      {
        repo: "Games",
        issue: 5,
        title: "Games task",
        url: "https://github.com/D-sorganization/Games/issues/5",
        labels: [],
        priority: "",
        updated_at: "2026-09-01T12:00:00Z",
      },
    ],
    excluded: [],
    warnings: [],
    generated_at: "2026-11-04T00:00:00Z",
  };

  function jobsReply(url: string) {
    if (url === "/api/coordination/jobs" || url.startsWith("/api/coordination/jobs?")) {
      const q = new URL(url, "http://local");
      const repo = q.searchParams.get("repo");
      let jobs = JOBS.jobs;
      if (repo) jobs = jobs.filter((j) => j.repo.toLowerCase() === repo.toLowerCase());
      return { status: 200, body: { ...JOBS, jobs } };
    }
    if (url === "/api/auth/me") {
      return {
        status: 200,
        body: {
          id: "fleet-operator",
          type: "human",
          name: "Operator",
          roles: ["operator"],
          scopes: ["coordination.write", "fleet.read"],
        },
      };
    }
    return undefined;
  }

  it("renders jobs and filters by repo and priority", async () => {
    const fetchMock = stubFetch(jobsReply);
    const baseFetch = fetchMock.getMockImplementation()!;
    // Slow jobs reply, as on a loaded CI runner: the panel frame and its repo
    // <select> render before the jobs (and so the repo options) arrive.
    fetchMock.mockImplementation(async (url: string, opts?: RequestInit) => {
      if (url.startsWith("/api/coordination/jobs")) await new Promise((r) => setTimeout(r, 50));
      return baseFetch(url, opts);
    });
    render(<FleetCommandPage />);
    openSection("Claims");
    // Wait for rows, not the frame: repo options come from the jobs data, and
    // choosing "Tools" before it exists leaves the select on "All repos".
    await waitFor(() => expect(screen.getByTestId("job-row-Games-5")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Filter jobs by repo"), { target: { value: "Tools" } });
    await waitFor(() => expect(screen.queryByTestId("job-row-Games-5")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("job-row-Tools-10")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Filter jobs by priority"), { target: { value: "high" } });
    expect(screen.getByTestId("job-row-Tools-10")).toBeInTheDocument();
    expect(screen.queryByTestId("job-row-Tools-11")).not.toBeInTheDocument();
  });

  it("ignores a stale unfiltered jobs fetch after the repo filter refetch", async () => {
    let releaseInitial: () => void = () => {};
    const initialGate = new Promise<void>((resolve) => {
      releaseInitial = resolve;
    });
    const fetchMock = stubFetch(jobsReply);
    const baseFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, opts?: RequestInit) => {
      const bareJobs = url === "/api/coordination/jobs";
      const allJobs = bareJobs || (url.startsWith("/api/coordination/jobs?") && !url.includes("repo="));
      if (allJobs) {
        await initialGate;
      }
      return baseFetch(url, opts);
    });
    render(<FleetCommandPage />);
    openSection("Claims");
    fireEvent.change(screen.getByLabelText("Filter jobs by repo"), { target: { value: "Tools" } });
    await waitFor(() => expect(screen.queryByTestId("job-row-Games-5")).not.toBeInTheDocument());
    releaseInitial();
    await waitFor(() => expect(screen.queryByTestId("job-row-Games-5")).not.toBeInTheDocument());
  });

  it("shows degraded banner when unavailable or incomplete", async () => {
    stubFetch((url) => {
      if (url === "/api/coordination/jobs" || url.startsWith("/api/coordination/jobs?")) {
        return {
          status: 200,
          body: { available: false, complete: false, jobs: [], warnings: ["holds list unreadable"], generated_at: "x" },
        };
      }
      return jobsReply(url);
    });
    render(<FleetCommandPage />);
    openSection("Claims");
    await waitFor(() => expect(screen.getByTestId("job-board-degraded")).toHaveTextContent(/unavailable|holds/i));
  });

  it("shows 409 reason when a claim loses", async () => {
    stubFetch((url, opts) => {
      const jobs = jobsReply(url);
      if (jobs) return jobs;
      if (url === "/api/coordination/claims" && opts?.method === "POST") {
        return {
          status: 409,
          body: { detail: { error: "issue is claimed", held_by: "gemini", guidance: "pick another issue" } },
        };
      }
      return undefined;
    });
    render(<FleetCommandPage />);
    openSection("Claims");
    await waitFor(() => expect(screen.getByTestId("job-row-Tools-10")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Claim Tools#10" }));
    await waitFor(() =>
      expect(screen.getByTestId("job-claim-error-Tools-10")).toHaveTextContent(
        "issue is claimed — held by gemini — pick another issue",
      ),
    );
  });
});
