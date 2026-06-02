// @vitest-environment jsdom
/**
 * Behaviour tests for pages/ClineLauncher.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836).
 *
 * Covers:
 * 1. Smoke render.
 * 2. Running vs stopped scheduler badge + Start/Stop disabled logic.
 * 3. Renders the agents table from status.
 * 4. "No agents configured" empty state.
 * 5. Repos 503 ⇒ "not installed" error banner.
 * 6. Start scheduler POSTs to the launcher endpoint.
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
import { ClineLauncherTab } from "../ClineLauncher";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const RUNNING_STATUS = {
  scheduler_running: true,
  scheduler_pid: 4242,
  runtime_root: "/home/user/.cline",
  agents: [
    {
      name: "issue-fixer",
      enabled: true,
      interval_seconds: 300,
      last_run_iso: "2026-06-01T10:00:00Z",
      last_repo: "runner-dashboard",
      last_window_pid: 1234,
      lock_alive: false,
    },
  ],
};

const STOPPED_STATUS = {
  scheduler_running: false,
  agents: [],
};

const REPOS = {
  count: 2,
  org_filter: "D-sorganization",
  wsl_distro: "Ubuntu",
  repos_root: "/repos",
  repos: [
    { name: "runner-dashboard", wsl_path: "/repos/runner-dashboard" },
    { name: "Maxwell-Daemon", wsl_path: "/repos/Maxwell-Daemon" },
  ],
};

/** Route mock fetch by URL so status/repos return distinct payloads. */
function mockFetchByUrl(opts: {
  status?: object;
  repos?: object;
  reposStatus?: number;
  /** Keep the status fetch pending so it never clears an error set by repos. */
  statusPending?: boolean;
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/repos")) {
        return Promise.resolve({
          ok: (opts.reposStatus ?? 200) < 400,
          status: opts.reposStatus ?? 200,
          json: () => Promise.resolve(opts.repos ?? REPOS),
        } as Response);
      }
      // status endpoint (and POST actions) default
      if (opts.statusPending) return new Promise<Response>(() => {});
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(opts.status ?? STOPPED_STATUS),
      } as Response);
    }),
  );
}

describe("ClineLauncherTab", () => {
  it("renders without throwing (smoke)", () => {
    mockFetchByUrl({});
    expect(() => render(<ClineLauncherTab />)).not.toThrow();
  });

  it("shows the running badge and disables Start when scheduler runs", async () => {
    mockFetchByUrl({ status: RUNNING_STATUS, repos: REPOS });
    render(<ClineLauncherTab />);
    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument());
    expect(screen.getByText(/pid 4242/)).toBeInTheDocument();
    const start = screen.getByRole("button", { name: /start scheduler/i });
    expect(start).toBeDisabled();
    const stop = screen.getByRole("button", { name: /stop scheduler/i });
    expect(stop).not.toBeDisabled();
  });

  it("renders the agents table from status", async () => {
    mockFetchByUrl({ status: RUNNING_STATUS, repos: REPOS });
    render(<ClineLauncherTab />);
    await waitFor(() =>
      expect(screen.getByText("issue-fixer")).toBeInTheDocument(),
    );
    // "runner-dashboard" appears both as the agent's last repo and in the
    // discovered-repos chip list, so assert at least one occurrence.
    expect(screen.getAllByText("runner-dashboard").length).toBeGreaterThan(0);
    expect(screen.getByText("300s")).toBeInTheDocument();
  });

  it("shows the empty state when no agents are configured", async () => {
    mockFetchByUrl({ status: STOPPED_STATUS, repos: REPOS });
    render(<ClineLauncherTab />);
    await waitFor(() =>
      expect(screen.getByText(/no agents configured/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("stopped")).toBeInTheDocument();
  });

  it("surfaces a 'not installed' error when /repos returns 503", async () => {
    mockFetchByUrl({ statusPending: true, reposStatus: 503 });
    render(<ClineLauncherTab />);
    await waitFor(() =>
      expect(
        screen.getByText(/launcher not installed on this machine/i),
      ).toBeInTheDocument(),
    );
  });

  it("POSTs to start the scheduler when Start is clicked", async () => {
    mockFetchByUrl({ status: STOPPED_STATUS, repos: REPOS });
    render(<ClineLauncherTab />);
    await waitFor(() => expect(screen.getByText("stopped")).toBeInTheDocument());
    const start = screen.getByRole("button", { name: /start scheduler/i });
    fireEvent.click(start);
    await waitFor(() => {
      const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
      const calledStart = fetchMock.mock.calls.some(
        (c: unknown[]) =>
          typeof c[0] === "string" &&
          (c[0] as string).includes("/api/agent-launcher/start"),
      );
      expect(calledStart).toBe(true);
    });
  });
});
