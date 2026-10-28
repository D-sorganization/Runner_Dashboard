// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActivityPage } from "../Activity/ActivityPage";

const SESSIONS_PAGE = {
  items: [
    {
      source: "staff_run",
      session_id: "claude-a-s1",
      node: "node-a",
      status: "live",
      repo: "Runner_Dashboard",
      branch: "claude/2864-activity-chat-ui",
      issue: 2864,
      pr: null,
      last_kind: "heartbeat",
      last_seq: 3,
      last_event_id: "evt-3",
      last_event_at: "2026-10-28T18:00:00Z",
    },
    {
      source: "coordination",
      session_id: "codex-g-s1",
      node: "node-a",
      status: "stale",
      repo: "Runner_Dashboard",
      branch: null,
      issue: null,
      pr: null,
      last_kind: "presence_release",
      last_seq: 1,
      last_event_id: "evt-1",
      last_event_at: "2026-10-28T10:00:00Z",
    },
  ],
  scan_limit: 4000,
  truncated: false,
};

const EVENTS_PAGE = {
  items: [
    {
      schema_version: 1,
      event_id: "evt-2",
      source: "staff_run",
      principal: "claude-a-s1",
      node: "node-a",
      session_id: "claude-a-s1",
      seq: 2,
      ts: "2026-10-28T17:59:00Z",
      repo: "Runner_Dashboard",
      issue: 2864,
      branch: "claude/2864-activity-chat-ui",
      pr: null,
      kind: "started",
      redaction_version: 1,
      payload: {},
    },
    {
      schema_version: 1,
      event_id: "evt-3",
      source: "staff_run",
      principal: "claude-a-s1",
      node: "node-a",
      session_id: "claude-a-s1",
      seq: 3,
      ts: "2026-10-28T18:00:00Z",
      repo: "Runner_Dashboard",
      issue: 2864,
      branch: "claude/2864-activity-chat-ui",
      pr: null,
      kind: "heartbeat",
      redaction_version: 1,
      payload: {},
    },
    {
      schema_version: 1,
      event_id: "evt-gap-1",
      source: "staff_run",
      principal: "claude-a-s1",
      node: "node-a",
      session_id: "claude-a-s1",
      seq: 4,
      ts: "2026-10-28T18:01:00Z",
      repo: "Runner_Dashboard",
      issue: 2864,
      branch: "claude/2864-activity-chat-ui",
      pr: null,
      kind: "OverflowGap",
      redaction_version: 1,
      payload: { reason: "spool full" },
    },
  ],
  next_cursor: null,
};

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const ok = { ok: true, status: 200 } as Response;
      if (String(url).includes("/activity/sessions")) {
        return Promise.resolve({ ...ok, json: async () => SESSIONS_PAGE } as Response);
      }
      return Promise.resolve({ ...ok, json: async () => EVENTS_PAGE } as Response);
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("ActivityPage", () => {
  it("groups sessions by node and status with counts", async () => {
    stubFetch();
    render(<ActivityPage />);
    await waitFor(() => expect(screen.getByText("node-a")).toBeInTheDocument());
    expect(screen.getByText("live (1)")).toBeInTheDocument();
    expect(screen.getByText("stale (1)")).toBeInTheDocument();
    expect(screen.getByText(/claude-a-s1/)).toBeInTheDocument();
  });

  it("has no composer or input affordance (read-only)", async () => {
    stubFetch();
    render(<ActivityPage />);
    await waitFor(() => expect(screen.getByText("node-a")).toBeInTheDocument());
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("selecting a session loads its scrollback and flags gap events, and the selection is URL-addressable", async () => {
    stubFetch();
    const user = userEvent.setup();
    render(<ActivityPage />);
    await waitFor(() => expect(screen.getByText(/claude-a-s1/)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /claude-a-s1/ }));

    await waitFor(() => expect(screen.getByText("started")).toBeInTheDocument());
    expect(screen.getByText("heartbeat")).toBeInTheDocument();
    expect(screen.getByText("OverflowGap")).toBeInTheDocument();
    expect(screen.getByText(/— gap/)).toBeInTheDocument();
    expect(window.location.search).toBe("?session=staff_run%3Aclaude-a-s1");
  });

  it("restores the selected session from the URL on mount", async () => {
    window.history.replaceState(null, "", "/?session=staff_run%3Aclaude-a-s1");
    stubFetch();
    render(<ActivityPage />);
    await waitFor(() => expect(screen.getByText("started")).toBeInTheDocument());
  });

  it("merges repeated polls of the same session by event_id without duplicating rows", async () => {
    stubFetch();
    const user = userEvent.setup();
    render(<ActivityPage />);
    await waitFor(() => expect(screen.getByText(/claude-a-s1/)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /claude-a-s1/ }));
    await waitFor(() => expect(screen.getByText("started")).toBeInTheDocument());

    expect(screen.getAllByText("heartbeat")).toHaveLength(1);
  });

  it("shows a degraded message when the sessions feed fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }));
    render(<ActivityPage />);
    await waitFor(() => expect(screen.getByText(/Activity sessions unavailable/)).toBeInTheDocument());
  });
});
