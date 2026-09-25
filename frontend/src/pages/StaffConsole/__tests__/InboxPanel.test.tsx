/**
 * InboxPanel.test.tsx — Unit tests for Staff Console Waiting on you inbox (SC-C5, Issue #1328).
 */

import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { InboxPanel } from "../InboxPanel";
import type { WaitingOnYouInboxResponse, WaitingOnYouItem } from "../inboxTypes";

const MOCK_ITEMS: WaitingOnYouItem[] = [
  {
    id: "app-1",
    category: "approval",
    title: "Approval required: Merge PR #1420",
    summary: "Context pane feature ready for merge",
    source: "proposals",
    severity: "high",
    action_url: "/staff?thread=th-1&proposal=p-1",
    thread_id: "th-1",
    created_at: "2026-09-25T13:00:00Z",
  },
  {
    id: "q-1",
    category: "question",
    title: "Question from ad-hoc",
    summary: "Should we use PostgreSQL or SQLite WAL?",
    source: "runs",
    severity: "high",
    action_url: "/staff?thread=th-2&run_id=run-q-1",
    thread_id: "th-2",
    created_at: "2026-09-25T13:05:00Z",
  },
  {
    id: "esc-1",
    category: "escalation",
    title: "Escalation: Maintenance hung on ControlTower",
    summary: "Process unkillable, requires operator intervention",
    source: "runs",
    severity: "critical",
    action_url: "/staff?thread=th-3&run_id=run-esc-1",
    thread_id: "th-3",
    created_at: "2026-09-25T13:10:00Z",
  },
  {
    id: "proj-1",
    category: "project_decision",
    title: "Project Decision needed: Runner_Dashboard",
    summary: "Decide on VHDX compaction retention policy",
    source: "projects",
    severity: "medium",
    action_url: "/projects/Runner_Dashboard",
    created_at: "2026-09-25T13:15:00Z",
  },
  {
    id: "board-1",
    category: "board_proposal",
    title: "[Board Proposal] Adopt tantivy micro-indexer",
    summary: "Board proposal awaiting owner review",
    source: "board_proposals",
    severity: "high",
    action_url: "/staff?thread=th-4&work_item=wi-1",
    thread_id: "th-4",
    created_at: "2026-09-25T13:20:00Z",
  },
  {
    id: "auth-1",
    category: "auth_signin",
    title: "Sign-in required: claude on OGLaptop",
    summary: "Run `claude auth login` on node OGLaptop",
    source: "auth",
    severity: "critical",
    action_url: "/settings/credentials",
    metadata: { login_command: "claude auth login" },
    created_at: "2026-09-25T13:25:00Z",
  },
];

const MOCK_INBOX: WaitingOnYouInboxResponse = {
  count: 6,
  counts: {
    approvals: 1,
    questions: 1,
    escalations: 1,
    project_decisions: 1,
    board_proposals: 1,
    auth_signins: 1,
  },
  items: MOCK_ITEMS,
  sources: {
    approvals: { status: "ok", count: 1 },
    questions: { status: "ok", count: 1 },
    escalations: { status: "ok", count: 1 },
    project_decisions: { status: "ok", count: 1 },
    board_proposals: { status: "ok", count: 1 },
    auth_signins: { status: "ok", count: 1 },
  },
};

describe("InboxPanel Component", () => {
  it("renders header, total count, and all items by default", () => {
    render(<InboxPanel inbox={MOCK_INBOX} />);

    expect(screen.getByRole("heading", { name: /waiting on you/i })).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();

    expect(screen.getByText(/Approval required: Merge PR #1420/i)).toBeInTheDocument();
    expect(screen.getByText(/Question from ad-hoc/i)).toBeInTheDocument();
    expect(screen.getByText(/Escalation: Maintenance hung on ControlTower/i)).toBeInTheDocument();
    expect(screen.getByText(/Project Decision needed: Runner_Dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/\[Board Proposal\] Adopt tantivy micro-indexer/i)).toBeInTheDocument();
    expect(screen.getByText(/Sign-in required: claude on OGLaptop/i)).toBeInTheDocument();
  });

  it("filters items by category tab", () => {
    render(<InboxPanel inbox={MOCK_INBOX} />);

    // Click "Escalations" tab
    const escTab = screen.getByRole("button", { name: /escalations/i });
    fireEvent.click(escTab);

    // Only escalation should be visible
    expect(screen.getByText(/Escalation: Maintenance hung on ControlTower/i)).toBeInTheDocument();
    expect(screen.queryByText(/Approval required: Merge PR #1420/i)).not.toBeInTheDocument();

    // Click "Approvals" tab
    const appTab = screen.getByRole("button", { name: /approvals/i });
    fireEvent.click(appTab);

    expect(screen.getByText(/Approval required: Merge PR #1420/i)).toBeInTheDocument();
    expect(screen.queryByText(/Escalation: Maintenance hung on ControlTower/i)).not.toBeInTheDocument();
  });

  it("displays warning banner when a source is unavailable", () => {
    const degradedInbox: WaitingOnYouInboxResponse = {
      ...MOCK_INBOX,
      sources: {
        ...MOCK_INBOX.sources,
        project_decisions: {
          status: "unavailable",
          count: 0,
          error: "GitHub connection timeout",
        },
      },
    };

    render(<InboxPanel inbox={degradedInbox} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/Some sources unavailable: project_decisions/i)).toBeInTheDocument();
  });

  it("handles onGenerateBriefing click and disabled state", () => {
    const onBrief = vi.fn();
    const { rerender } = render(
      <InboxPanel inbox={MOCK_INBOX} onGenerateBriefing={onBrief} isGeneratingBriefing={false} />
    );

    const briefBtn = screen.getByRole("button", { name: /generate barb briefing/i });
    fireEvent.click(briefBtn);
    expect(onBrief).toHaveBeenCalledTimes(1);

    rerender(
      <InboxPanel inbox={MOCK_INBOX} onGenerateBriefing={onBrief} isGeneratingBriefing={true} />
    );
    expect(screen.getByRole("button", { name: /generate barb briefing/i })).toBeDisabled();
    expect(screen.getByText("Generating...")).toBeInTheDocument();
  });

  it("handles onRefresh click", () => {
    const onRefresh = vi.fn();
    render(<InboxPanel inbox={MOCK_INBOX} onRefresh={onRefresh} />);

    const refreshBtn = screen.getByRole("button", { name: /refresh inbox/i });
    fireEvent.click(refreshBtn);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("calls onOpenItem when clicking action button", () => {
    const onOpen = vi.fn();
    render(<InboxPanel inbox={MOCK_INBOX} onOpenItem={onOpen} />);

    const decideBtn = screen.getByRole("button", { name: /review & decide/i });
    fireEvent.click(decideBtn);
    expect(onOpen).toHaveBeenCalledWith(MOCK_ITEMS[0]);
  });

  it("renders empty state when there are no items", () => {
    const emptyInbox: WaitingOnYouInboxResponse = {
      count: 0,
      counts: {},
      items: [],
      sources: {},
    };

    render(<InboxPanel inbox={emptyInbox} />);
    expect(screen.getByText(/all clear! nothing currently waiting on you/i)).toBeInTheDocument();
  });
});
