// @vitest-environment jsdom
/**
 * Tests for RunnerAudit.tsx — decomposition #836 pass 2.
 *
 * Covers the extracted "Runner Audit" tab behaviour:
 * 1. Smoke render.
 * 2. "Not yet checked" empty state before any audit has run.
 * 3. "No violations" success state once checked with zero violations.
 * 4. Violations table rendered with row data + run link.
 * 5. Error banner shown when the payload carries an error.
 * 6. Refresh button invokes onRefresh.
 */
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  render,
  screen,
  fireEvent,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RunnerAudit, { RunnerAuditPage } from "../RunnerAudit";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function headersOf(init: RequestInit): Record<string, string> {
  const headers = init.headers;
  if (headers instanceof Headers) {
    const out: Record<string, string> = {};
    headers.forEach((value, key) => {
      out[key.toLowerCase()] = value;
    });
    return out;
  }
  return Object.fromEntries(
    Object.entries((headers ?? {}) as Record<string, string>).map(
      ([key, value]) => [key.toLowerCase(), value],
    ),
  );
}

async function flushAsyncWork(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("RunnerAudit", () => {
  it("renders without throwing (smoke test)", () => {
    expect(() =>
      render(<RunnerAudit audit={{ violations: [] }} onRefresh={() => {}} />),
    ).not.toThrow();
  });

  it("shows the audit heading", () => {
    render(<RunnerAudit audit={{ violations: [] }} onRefresh={() => {}} />);
    expect(
      screen.getByText(/Hosted-Runner Billing Audit/i),
    ).toBeInTheDocument();
  });

  it("shows the not-yet-checked state when last_checked is null", () => {
    render(
      <RunnerAudit
        audit={{ violations: [], last_checked: null }}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText(/Audit has not run yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Not yet checked/i)).toBeInTheDocument();
    expect(document.querySelector(".empty-state")).toBeInTheDocument();
    expect(
      document.querySelector("[data-touch-primitive='Badge']"),
    ).toHaveTextContent("Not yet checked");
  });

  it("shows the all-clear state when checked with no violations", () => {
    render(
      <RunnerAudit
        audit={{ violations: [], last_checked: "2026-06-01T00:00:00Z" }}
        onRefresh={() => {}}
      />,
    );
    expect(
      screen.getByText(/No hosted-runner violations detected/i),
    ).toBeInTheDocument();
  });

  it("renders a violations table with row data and a run link", () => {
    render(
      <RunnerAudit
        audit={{
          last_checked: "2026-06-01T00:00:00Z",
          violations: [
            {
              repo: "D-sorg/foo",
              workflow: "ci.yml",
              job_name: "build",
              runner_name: "ubuntu-latest",
              started_at: "2026-06-01T00:00:00Z",
              run_url: "https://example.com/run/1",
            },
          ],
        }}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText(/1 violation\(s\) found/i)).toBeInTheDocument();
    expect(screen.getByText("D-sorg/foo")).toBeInTheDocument();
    expect(screen.getByText("ubuntu-latest")).toBeInTheDocument();
    expect(document.querySelector(".runner-audit__table")).toBeInTheDocument();
    expect(
      document.querySelector(".runner-audit__runner-badge"),
    ).toHaveAttribute("data-touch-primitive", "Badge");
    const link = screen.getByRole("link", { name: /View Run/i });
    expect(link).toHaveAttribute("href", "https://example.com/run/1");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("falls back to runner_group then 'unknown' for the runner cell", () => {
    render(
      <RunnerAudit
        audit={{
          last_checked: "2026-06-01T00:00:00Z",
          violations: [{ repo: "a", runner_group: "group-x" }, { repo: "b" }],
        }}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText("group-x")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("shows an error banner when the payload carries an error", () => {
    render(
      <RunnerAudit
        audit={{ violations: [], error: "boom" }}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText(/Error: boom/i)).toBeInTheDocument();
  });

  it("invokes onRefresh when the Refresh button is clicked", () => {
    const onRefresh = vi.fn();
    render(<RunnerAudit audit={{ violations: [] }} onRefresh={onRefresh} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Refresh runner audit now/i }),
    );
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".runner-audit__refresh")).toHaveAttribute(
      "data-touch-primitive",
      "TouchButton",
    );
  });

  it("loads audit data from the current runner-routing endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse({
        last_checked: "2026-06-15T00:00:00Z",
        violations: [
          {
            repo: "D-sorganization/Runner_Dashboard",
            workflow: "ci.yml",
            job_name: "tests",
            runner_name: "ubuntu-latest",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<RunnerAuditPage />);

    expect(
      await screen.findByText("D-sorganization/Runner_Dashboard"),
    ).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/runner-routing-audit");
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(headersOf(init)["x-requested-with"]).toBe("XMLHttpRequest");
  });

  it("refreshes through the runner-routing refresh endpoint", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ violations: [], last_checked: null, error: null }),
      )
      .mockResolvedValueOnce(jsonResponse({ accepted: true }))
      .mockResolvedValueOnce(
        jsonResponse({
          last_checked: "2026-06-15T00:00:00Z",
          violations: [{ repo: "D-sorganization/Tools" }],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<RunnerAuditPage />);
    await flushAsyncWork();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.click(
      screen.getByRole("button", { name: /Refresh runner audit now/i }),
    );
    await flushAsyncWork();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [refreshUrl, refreshInit] = fetchMock.mock.calls[1] as [
      string,
      RequestInit,
    ];
    expect(refreshUrl).toBe("/api/runner-routing-audit/refresh");
    expect(refreshInit.method).toBe("POST");
    expect(headersOf(refreshInit)["x-requested-with"]).toBe("XMLHttpRequest");

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    await flushAsyncWork();

    expect(screen.getByText("D-sorganization/Tools")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
