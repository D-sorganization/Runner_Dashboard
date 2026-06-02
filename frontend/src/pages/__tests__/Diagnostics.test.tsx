// @vitest-environment jsdom
/**
 * Behaviour tests for pages/Diagnostics.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836).
 *
 * Covers:
 * 1. Loading state before the parallel fetches resolve.
 * 2. Renders the System Overview from the summary.
 * 3. Drift banner shows when deployed version is behind origin/main.
 * 4. Restart is gated behind a confirm step that POSTs on confirm.
 * 5. Error state when a fetch rejects (orthogonality — no crash).
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
import { DiagnosticsTab } from "../Diagnostics";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const SUMMARY = {
  dashboard_pid: 9001,
  dashboard_memory_mb: 128,
  dashboard_port: 8321,
  git_commit: "abc1234",
  wsl_available: true,
  wsl_status: "Running",
};

const DRIFT_CLEAN = { is_drifted: false };
const DRIFT_BEHIND = {
  is_drifted: true,
  source_commit: "abc1234",
  remote_commit: "def5678",
};

function mockFetchByUrl(opts: {
  summary?: object;
  drift?: object;
  restart?: object;
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/api/diagnostics/restart-service")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(opts.restart ?? { success: true }),
        } as Response);
      }
      if (url.includes("git-drift")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(opts.drift ?? DRIFT_CLEAN),
        } as Response);
      }
      // diagnostics summary
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(opts.summary ?? SUMMARY),
      } as Response);
    }),
  );
}

describe("DiagnosticsTab", () => {
  it("shows the loading state before fetch resolves", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<DiagnosticsTab />);
    expect(screen.getByText(/loading diagnostics/i)).toBeInTheDocument();
  });

  it("renders the system overview after fetch", async () => {
    mockFetchByUrl({ summary: SUMMARY, drift: DRIFT_CLEAN });
    render(<DiagnosticsTab />);
    await waitFor(() =>
      expect(screen.getByText("System Overview")).toBeInTheDocument(),
    );
    expect(screen.getByText("9001")).toBeInTheDocument();
    expect(screen.getByText("abc1234")).toBeInTheDocument();
    expect(screen.getByText("Up to date")).toBeInTheDocument();
  });

  it("shows the drift banner when behind origin/main", async () => {
    mockFetchByUrl({ summary: SUMMARY, drift: DRIFT_BEHIND });
    render(<DiagnosticsTab />);
    await waitFor(() =>
      expect(
        screen.getByText(/deployed version is behind origin\/main/i),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("Behind origin/main")).toBeInTheDocument();
  });

  it("gates restart behind a confirm step that POSTs on confirm", async () => {
    mockFetchByUrl({ summary: SUMMARY, drift: DRIFT_CLEAN, restart: { success: true } });
    render(<DiagnosticsTab />);
    await waitFor(() =>
      expect(screen.getByText("Recovery Actions")).toBeInTheDocument(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /restart dashboard service/i }),
    );
    const confirm = screen.getByRole("button", { name: /confirm restart/i });
    fireEvent.click(confirm);
    await waitFor(() => {
      const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
      const calledRestart = fetchMock.mock.calls.some(
        (c: unknown[]) =>
          typeof c[0] === "string" &&
          (c[0] as string).includes("/api/diagnostics/restart-service"),
      );
      expect(calledRestart).toBe(true);
    });
    await waitFor(() =>
      expect(
        screen.getByText(/service restarted successfully/i),
      ).toBeInTheDocument(),
    );
  });

  it("renders an error state when a fetch rejects", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network down"))),
    );
    render(<DiagnosticsTab />);
    await waitFor(() =>
      expect(screen.getByText(/error:/i)).toBeInTheDocument(),
    );
  });
});
