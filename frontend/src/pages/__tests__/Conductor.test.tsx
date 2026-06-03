// @vitest-environment jsdom
/**
 * Behaviour tests for pages/Conductor.tsx — Conductor dashboard integration
 * (Repository_Management epic #1273, issue #1282).
 *
 * Covers:
 * 1. Loading state before the queue fetch resolves.
 * 2. Renders mode, capacity, provider mix, and budget burn after fetch.
 * 3. Renders planned/active/blocked work counts.
 * 4. Pause control POSTs {action:"pause"} with the CSRF header.
 * 5. Disabled (feature-flag-off) surface renders an inert notice, not an error.
 * 6. Error state when the API call fails (orthogonality — no crash).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Conductor } from "../Conductor";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const MOCK_QUEUE = {
  enabled: true,
  mode: "running",
  active_leases: 2,
  reserved_slots: 3,
  capacity: {
    idle_runners: 4,
    online_runners: 8,
    busy_runners: 4,
    total_runners: 10,
  },
  work: {
    planned: 5,
    active: 2,
    blocked: 1,
  },
  provider_mix: {
    claude_code_cli: 2,
    codex: 1,
  },
  budget: {
    spent_usd: 12.5,
    limit_usd: 50,
  },
};

function mockFetch(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
      }),
    ),
  );
}

describe("Conductor", () => {
  it("shows loading state initially", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<Conductor />);
    expect(screen.getByText(/loading conductor/i)).toBeInTheDocument();
  });

  it("renders mode, capacity and budget after fetch", async () => {
    mockFetch(MOCK_QUEUE);
    render(<Conductor />);
    await waitFor(() => expect(screen.getByText(/running/i)).toBeInTheDocument());
    expect(document.querySelector(".conductor__mode")).toHaveAttribute(
      "data-touch-primitive",
      "Badge",
    );
    // capacity section surfaced
    expect(screen.getByText("Fleet capacity")).toBeInTheDocument();
    // budget burn surfaced (dollar amounts)
    expect(screen.getByText(/\$12\.50/)).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: /conductor budget burn/i }),
    ).toHaveAttribute("value", "25");
  });

  it("renders planned / active / blocked work", async () => {
    mockFetch(MOCK_QUEUE);
    render(<Conductor />);
    await waitFor(() => expect(screen.getByTestId("work-planned")).toHaveTextContent("5"));
    expect(screen.getByTestId("work-active")).toHaveTextContent("2");
    expect(screen.getByTestId("work-blocked")).toHaveTextContent("1");
    expect(document.querySelector(".conductor__stat--accent")).toContainElement(
      screen.getByTestId("work-blocked"),
    );
  });

  it("pause control POSTs the pause action with CSRF header", async () => {
    const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...MOCK_QUEUE, mode: "paused" }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(MOCK_QUEUE),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Conductor />);
    await waitFor(() => expect(screen.getByRole("button", { name: /pause/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /pause/i }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([, opts]) => (opts as RequestInit | undefined)?.method === "POST",
      );
      expect(postCall).toBeTruthy();
      const [postUrl, postOpts] = postCall as [string, RequestInit];
      expect(postUrl).toBe("/api/orchestrator/queue");
      expect(JSON.parse(postOpts.body as string)).toEqual({ action: "pause" });
      expect((postOpts.headers as Record<string, string>)["X-Requested-With"]).toBe("XMLHttpRequest");
    });
    expect(screen.getByRole("button", { name: /pause/i })).toHaveAttribute(
      "data-touch-primitive",
      "TouchButton",
    );
  });

  it("renders an inert notice when the surface is disabled", async () => {
    mockFetch({ detail: "Conductor integration is disabled" }, 404);
    render(<Conductor />);
    await waitFor(() => expect(screen.getByText(/conductor integration is disabled/i)).toBeInTheDocument());
    expect(document.querySelector(".empty-state")).toBeInTheDocument();
  });

  it("shows an error state when the fetch rejects (no crash)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("network error"))));
    render(<Conductor />);
    await waitFor(() => expect(screen.getByText(/failed to load conductor/i)).toBeInTheDocument());
    expect(document.querySelector(".empty-state")).toHaveAttribute("data-variant", "error");
  });
});
