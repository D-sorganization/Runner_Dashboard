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

  it("shows stale-session guidance, not the empty-forever message, when every known session has expired (#3504)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          agents: [],
          threads: [],
          activity: [],
          backlog: {
            stale_sessions: [{ session: "local-stale-1", role: "local", node: "node-c" }],
            blocked: [],
          },
          empty_reason: "local_empty",
        }),
      }),
    );
    render(<AgentBusPage />);
    await waitFor(() => expect(screen.getByText(/known session.*stale or expired/i)).toBeInTheDocument());
    expect(screen.getByText("local-stale-1")).toBeInTheDocument();
    expect(screen.queryByText(/Agents appear after session-start hook registration/)).not.toBeInTheDocument();
  });

  it("keeps the registration-unavailable message when nothing has ever registered (#3504)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          agents: [],
          threads: [],
          activity: [],
          backlog: { stale_sessions: [], blocked: [] },
          empty_reason: "complete_empty",
        }),
      }),
    );
    render(<AgentBusPage />);
    await waitFor(() => expect(screen.getByText(/No live sessions/)).toBeInTheDocument());
    expect(screen.getByText(/Agents appear after session-start hook registration/)).toBeInTheDocument();
  });

  it("shows operator guidance for an unauthorized overview call, not a raw status string (#3504)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }),
    );
    render(<AgentBusPage />);
    await waitFor(() => expect(screen.getByText("Session expired")).toBeInTheDocument());
    expect(
      screen.getByText(/refresh it in Settings → Credentials/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Agent bus overview unavailable/)).not.toBeInTheDocument();
  });

  it("links a live agent row into the existing Activity session inspector (#3504)", async () => {
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
              lease_expires_at: "2099-01-01T00:00:00Z",
              inbox_preview: [],
            },
          ],
          threads: [],
          activity: [],
          backlog: { stale_sessions: [], blocked: [] },
          empty_reason: null,
        }),
      }),
    );
    render(<AgentBusPage />);
    const link = await screen.findByRole("link", { name: /local-node-c-1/ });
    expect(link.getAttribute("href")).toMatch(/session=agent_bus%3Alocal-node-c-1/);
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
    await waitFor(() => expect(screen.getByText("Couldn't load data")).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(30_000);
    await waitFor(() => expect(screen.getByText(/Live agents \(0\)/)).toBeInTheDocument());
    expect(screen.queryByText("Couldn't load data")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
