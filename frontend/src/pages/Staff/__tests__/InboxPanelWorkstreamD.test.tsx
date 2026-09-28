// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { InboxPanel } from "../InboxPanel";
import * as staffApi from "../staffApi";
import type { InboxAggregate } from "../inboxTypes";
import { getFocusable } from "../../../primitives/focusable";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const mockDiverseInbox: InboxAggregate = {
  count: 6,
  counts: {
    total: 6,
    approvals: 1,
    needs_input: 1,
    escalations: 1,
    project_decisions: 1,
    board_proposals: 0,
    auth_sign_ins: 2,
  },
  sources: {
    approval: { status: "ok", count: 1 },
    escalation: { status: "ok", count: 1 },
    needs_input: { status: "ok", count: 1 },
    auth_sign_in: { status: "ok", count: 2 },
    project_decision: { status: "ok", count: 1 },
  },
  generated_at: "2026-09-27T12:00:00Z",
  items: [
    {
      id: "dec_1",
      source: "project_decision",
      title: "UpstreamDrift: Decide dependency upgrade",
      summary: "Upgrade react to v19 or stay on v18",
      severity: "low",
      created_at: "2026-09-27T10:00:00Z",
      link: "/staff?repo=UpstreamDrift",
      metadata: { repo: "UpstreamDrift" },
    },
    {
      id: "auth_1",
      source: "auth_sign_in",
      title: "Claude sign-in required",
      summary: "Claude session expired on DeskComputer",
      severity: "high",
      created_at: "2026-09-27T10:30:00Z",
      link: "/settings/credentials",
    },
    {
      id: "auth_2",
      source: "auth_sign_in",
      title: "Codex token expired",
      summary: "Codex CLI needs refresh",
      severity: "high",
      created_at: "2026-09-27T10:35:00Z",
      link: "/settings/credentials",
    },
    {
      id: "app_1",
      source: "approval",
      title: "Approve deployment to staging",
      summary: "Deploy commit 7f83e2a to staging cluster",
      severity: "critical",
      created_at: "2026-09-27T11:00:00Z",
      link: "/staff?thread=th_deploy",
    },
    {
      id: "esc_1",
      source: "escalation",
      title: "Escalated: Worker node unresponsive",
      summary: "Node ControlTower failed heartbeat 3 times",
      severity: "high",
      created_at: "2026-09-27T10:50:00Z",
      link: "/staff?thread=th_node",
    },
    {
      id: "input_1",
      source: "needs_input",
      title: "Question: API key scope",
      summary: "Should the read token have repo-level scope?",
      severity: "medium",
      created_at: "2026-09-27T10:40:00Z",
      link: "/staff?run=run_scope",
    },
  ],
};

const mockDegradedInbox: InboxAggregate = {
  count: 1,
  counts: {
    total: 1,
    approvals: 1,
    needs_input: 0,
    escalations: 0,
    project_decisions: 0,
    board_proposals: 0,
    auth_sign_ins: 0,
  },
  sources: {
    approval: { status: "ok", count: 1 },
    board_proposals: { status: "unavailable", count: 0, error: "Database connection timed out after 5000ms" },
  },
  generated_at: "2026-09-27T12:00:00Z",
  items: [
    {
      id: "app_2",
      source: "approval",
      title: "Approve budget increase",
      summary: "Increase budget for worker",
      severity: "medium",
      created_at: "2026-09-27T11:55:00Z",
      link: "/staff?thread=th_budget",
    },
  ],
};

describe("Inbox Attention Panel & Drawer (Workstream D)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders collapsed bar with per-kind counts and an Open button", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDiverseInbox);
    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByText(/Waiting on you/i)).toBeInTheDocument();
      expect(screen.getByTestId("inbox-total-badge")).toHaveTextContent("6");
    });

    // Check per-kind chips in collapsed bar
    expect(screen.getByText(/Approvals 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Escalations 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Needs input 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Sign-ins 2/i)).toBeInTheDocument();
    expect(screen.getByText(/Decisions 1/i)).toBeInTheDocument();

    // Open button exists
    expect(screen.getByRole("button", { name: /open/i })).toBeInTheDocument();
  });

  it("opens drawer on Open click and closes via Escape key", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDiverseInbox);
    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open/i })).toBeInTheDocument();
    });

    // Drawer should not be open initially
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Click Open
    fireEvent.click(screen.getByRole("button", { name: /open/i }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute("aria-modal", "true");

    // Press Escape to close
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("traps Tab inside the drawer and restores focus to Open on close", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDiverseInbox);
    render(<InboxPanel />);

    const openBtn = await screen.findByRole("button", { name: /open/i });
    openBtn.focus();
    fireEvent.click(openBtn);

    const dialog = screen.getByRole("dialog");
    const focusable = getFocusable(dialog);
    expect(focusable.length).toBeGreaterThan(1);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    last.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(openBtn);
  });

  it("closes drawer on backdrop click", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDiverseInbox);
    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    const backdrop = screen.getByTestId("inbox-drawer-backdrop");
    fireEvent.click(backdrop);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sorts approvals first in the drawer item list", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDiverseInbox);
    render(<InboxPanel defaultOpen />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-item-list")).toBeInTheDocument();
    });

    const items = screen.getAllByTestId(/^inbox-item-(?!list)/);
    // First item should be the approval
    expect(items[0]).toHaveAttribute("data-testid", "inbox-item-app_1");
  });

  it("groups sign-in items under a collapsible Providers need sign-in row", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDiverseInbox);
    render(<InboxPanel defaultOpen />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-item-list")).toBeInTheDocument();
    });

    expect(screen.getByText(/Providers need sign-in \(2\)/i)).toBeInTheDocument();
  });

  it("renders degraded source banner with details disclosure", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDegradedInbox);
    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-degraded-banner")).toBeInTheDocument();
    });

    expect(screen.getByText(/1 source unavailable: board_proposals/i)).toBeInTheDocument();

    // Check details disclosure
    const disclosure = screen.getByText("Details");
    expect(disclosure).toBeInTheDocument();

    const errorMsg = screen.getByTestId("inbox-degraded-error-board_proposals");
    expect(errorMsg).toHaveTextContent("Database connection timed out after 5000ms");
  });

  it("contains no emoji chrome characters in rendered markup", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDiverseInbox);
    const { container } = render(<InboxPanel defaultOpen />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-item-list")).toBeInTheDocument();
    });

    const emojis = ["📋", "🎉", "⚠️", "▶", "▼"];
    for (const emoji of emojis) {
      expect(container.textContent).not.toContain(emoji);
    }
  });

  it("satisfies CSS contracts for drawer width and no glassmorphism hover", () => {
    const cssPath = path.join(__dirname, "../InboxPanel.css");
    const css = fs.readFileSync(cssPath, "utf8");

    // Drawer is 420px wide
    expect(css).toMatch(/420px/);
    // Must not have glass-card:hover or translucent hover repaint
    expect(css).not.toMatch(/\.glass-card:hover/);
  });
});

describe("InboxPanel grouped details (#1725)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the backend's aggregated sign-in and per-repo decision details", async () => {
    const aggregated: InboxAggregate = {
      ...mockDiverseInbox,
      count: 2,
      items: [
        {
          id: "auth_sign_in",
          source: "auth_sign_in",
          title: "2 providers need sign-in",
          summary: "Sign-in required for: Codex CLI, Cline.",
          severity: "medium",
          created_at: "2026-09-27T10:00:00Z",
          link: "/settings#credentials",
          details: [
            { provider: "codex", label: "Codex CLI", reason: "OPENAI_API_KEY not set" },
            { provider: "cline", label: "Cline", reason: "" },
          ],
        },
        {
          id: "project_dec_UpstreamDrift",
          source: "project_decision",
          title: "UpstreamDrift: 7 decisions needed",
          summary: "7 decisions needed in UpstreamDrift STATUS.md charter.",
          severity: "low",
          created_at: "2026-09-27T09:00:00Z",
          link: "/projects/UpstreamDrift",
          metadata: { repo: "UpstreamDrift" },
          details: ["d1", "d2", "d3", "d4", "d5", "d6", "d7"],
        },
      ],
    };
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(aggregated);
    render(<InboxPanel defaultOpen />);

    const auth = await screen.findByTestId("inbox-details-auth_sign_in");
    expect(auth).toHaveTextContent("Codex CLI: OPENAI_API_KEY not set");
    expect(auth).toHaveTextContent("Cline");

    const decisions = screen.getByTestId("inbox-details-project_dec_UpstreamDrift");
    expect(decisions.querySelectorAll("li")).toHaveLength(6);
    expect(decisions).toHaveTextContent("+2 more");
  });
});
