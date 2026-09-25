import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { InboxPanel } from "../../Staff/InboxPanel";
import * as staffApi from "../../Staff/staffApi";
import type { InboxAggregate } from "../../Staff/inboxTypes";

const mockInboxData: InboxAggregate = {
  count: 3,
  counts: {
    total: 3,
    approvals: 1,
    needs_input: 1,
    escalations: 1,
    project_decisions: 0,
    board_proposals: 0,
    auth_sign_ins: 0,
  },
  sources: {
    approvals: { status: "ok", count: 1 },
    needs_input: { status: "ok", count: 1 },
    escalations: { status: "ok", count: 1 },
    project_decisions: { status: "ok", count: 0 },
  },
  generated_at: "2026-09-25T12:00:00Z",
  items: [
    {
      id: "approval_1",
      source: "approval",
      title: "Approve runner restart",
      summary: "Restart unresponsive worker on oglaptop",
      severity: "high",
      created_at: "2026-09-25T11:55:00Z",
      link: "/staff?thread=th_restart",
    },
    {
      id: "needs_input_1",
      source: "needs_input",
      title: "Question from issue-remediator",
      summary: "Should we use double precision float?",
      severity: "medium",
      created_at: "2026-09-25T11:50:00Z",
      link: "/staff?run=run_10931",
    },
    {
      id: "escalation_1",
      source: "escalation",
      title: "Escalated: CI deadlock in Tools",
      summary: "Merge queue stalled across shards",
      severity: "critical",
      created_at: "2026-09-25T11:45:00Z",
      link: "/staff?thread=th_tools",
    },
  ],
};

const mockDegradedInboxData: InboxAggregate = {
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
    approvals: { status: "ok", count: 1 },
    project_decisions: { status: "unavailable", count: 0, error: "GitHub rate limit exceeded" },
  },
  generated_at: "2026-09-25T12:00:00Z",
  items: [
    {
      id: "approval_2",
      source: "approval",
      title: "Approve budget increase",
      summary: "Increase daily cap for pr-remediator",
      severity: "medium",
      created_at: "2026-09-25T11:55:00Z",
      link: "/staff?thread=th_budget",
    },
  ],
};

describe("InboxPanel (SC-C5, Issue #1328)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders aggregated items across sources with severity badges", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockInboxData);

    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByText("Waiting on You")).not.toBeNull();
      expect(screen.getByTestId("inbox-total-badge").textContent).toBe("3");
    });

    expect(screen.getByText("Approve runner restart")).not.toBeNull();
    expect(screen.getByText("Question from issue-remediator")).not.toBeNull();
    expect(screen.getByText("Escalated: CI deadlock in Tools")).not.toBeNull();

    expect(screen.getByText("CRITICAL")).not.toBeNull();
    expect(screen.getByText("HIGH")).not.toBeNull();
    expect(screen.getByText("MEDIUM")).not.toBeNull();
  });

  it("renders empty state when there are no waiting items", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue({
      count: 0,
      counts: {
        total: 0,
        approvals: 0,
        needs_input: 0,
        escalations: 0,
        project_decisions: 0,
        board_proposals: 0,
        auth_sign_ins: 0,
      },
      sources: { approvals: { status: "ok", count: 0 } },
      generated_at: "2026-09-25T12:00:00Z",
      items: [],
    });

    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-empty")).not.toBeNull();
    });
    expect(screen.getByText(/All clear!/i)).not.toBeNull();
  });

  it("shows degraded sources banner when a source is unavailable while rendering healthy items", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockDegradedInboxData);

    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-degraded-banner")).not.toBeNull();
    });

    expect(screen.getByText(/GitHub rate limit exceeded/)).not.toBeNull();
    expect(screen.getByText("Approve budget increase")).not.toBeNull();
  });

  it("filters items when clicking filter pills", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockInboxData);

    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByText("Approve runner restart")).not.toBeNull();
    });

    // Click 'Needs Input' filter tab
    const needsInputTab = screen.getByTestId("filter-tab-needs-input");
    fireEvent.click(needsInputTab);

    // Only needs_input item should be rendered
    expect(screen.queryByText("Approve runner restart")).toBeNull();
    expect(screen.getByText("Question from issue-remediator")).not.toBeNull();
    expect(screen.queryByText("Escalated: CI deadlock in Tools")).toBeNull();

    // Click 'All' filter tab
    const allTab = screen.getByTestId("filter-tab-all");
    fireEvent.click(allTab);

    expect(screen.getByText("Approve runner restart")).not.toBeNull();
    expect(screen.getByText("Question from issue-remediator")).not.toBeNull();
    expect(screen.getByText("Escalated: CI deadlock in Tools")).not.toBeNull();
  });

  it("triggers briefing generation when clicking Request Briefing button", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockInboxData);
    const briefingSpy = vi.spyOn(staffApi, "requestStaffBriefing").mockResolvedValue({
      ok: true,
      briefing_id: "msg_briefing_1",
      thread_id: "th_barb",
      kind: "on_demand",
      waiting_count: 3,
      body_md: "# Briefing",
    });

    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("request-briefing-btn")).not.toBeNull();
    });

    const btn = screen.getByTestId("request-briefing-btn");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(briefingSpy).toHaveBeenCalledWith("on_demand");
      expect(screen.getByTestId("briefing-status")).not.toBeNull();
      expect(screen.getByText(/Briefing posted to Barb's thread/)).not.toBeNull();
    });
  });

  it("calls onOpenRun when clicking an item link with run query parameter", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(mockInboxData);
    const onOpenRun = vi.fn();

    render(<InboxPanel onOpenRun={onOpenRun} />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-action-needs_input_1")).not.toBeNull();
    });

    const actionLink = screen.getByTestId("inbox-action-needs_input_1");
    fireEvent.click(actionLink);

    expect(onOpenRun).toHaveBeenCalledWith("run_10931");
  });
});
