// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentBusPage } from "../AgentBus/AgentBusPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentBusPage", () => {
  it("renders live agents from overview API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          agents: [
            {
              session: "local-node-c-1",
              role: "local",
              provider: "cursor",
              node: "node-c",
              lease_expires_at: "2026-10-22T12:00:00Z",
              inbox_preview: [{ id: "m1", type: "status", from_session: "local-node-c-1", body: "hello" }],
            },
          ],
          threads: [],
          activity: [],
          backlog: { stale_sessions: [], blocked: [] },
        }),
      }),
    );
    render(<AgentBusPage />);
    await waitFor(() => expect(screen.getByText("local-node-c-1")).toBeInTheDocument());
    expect(screen.getByText(/Live agents \(1\)/)).toBeInTheDocument();
  });

  it("clears error when a subsequent poll succeeds (RD-46)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("poll 1 network error"))
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          agents: [],
          threads: [],
          activity: [],
          backlog: { stale_sessions: [], blocked: [] },
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentBusPage />);
    await waitFor(() => expect(screen.getByText(/Agent bus overview unavailable/)).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(30_000);
    await waitFor(() => expect(screen.getByText(/Live agents \(0\)/)).toBeInTheDocument());
    expect(screen.queryByText(/Agent bus overview unavailable/)).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
