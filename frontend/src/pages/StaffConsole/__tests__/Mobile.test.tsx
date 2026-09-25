// @vitest-environment jsdom
/**
 * Mobile.test.tsx — Unit tests for Mobile Staff Console (SC-D8, Issue #1331).
 *
 * Covers:
 * 1. Initial roster view with Ask Barb top entry, role groups, and search.
 * 2. Full-screen navigation from roster to thread view and back via back button.
 * 3. Safe-area aware bottom composer and sending messages.
 * 4. Action proposals in thread with thumb-reachable Approve / Deny actions.
 * 5. Push deep links via ?thread=<id> and ?role=<role> query params.
 * 6. Role context pane drawer toggle.
 * 7. Tab switching to Waiting on You inbox.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StaffConsoleMobile } from "../Mobile";
import type { StaffRoleItem } from "../types";
import type { ThreadInfo, ThreadMessage } from "../threadTypes";

afterEach(cleanup);

const MOCK_ROLES: StaffRoleItem[] = [
  {
    name: "barb",
    title: "Barb",
    summary: "Fleet Orchestrator and conversational concierge",
    group: "leadership",
    valid: true,
    dispatchable: false,
    active_runs: 0,
    caller_unread_count: 2,
    last_message_preview: "All runners are healthy.",
    last_message_at: "2026-09-25T12:00:00Z",
  },
  {
    name: "architect",
    title: "Chief Architect",
    summary: "System design, ADR review, technical debt oversight",
    group: "specialists",
    valid: true,
    dispatchable: true,
    active_runs: 1,
    caller_unread_count: 0,
    last_message_preview: "Proposed caching layer.",
    last_message_at: "2026-09-25T11:00:00Z",
  },
  {
    name: "maintenance",
    title: "Fleet Maintenance",
    summary: "Health, compaction, disk hygiene, stalled runs",
    group: "operations",
    valid: true,
    dispatchable: true,
    active_runs: 0,
    caller_unread_count: 0,
  },
];

const MOCK_BARB_THREAD: ThreadInfo = {
  id: "thread-barb-auto",
  title: "Conversation with Barb",
  kind: "auto",
  participants: ["user", "barb"],
  status: "active",
};

const MOCK_MESSAGES: ThreadMessage[] = [
  {
    id: "msg-1",
    thread_id: "thread-barb-auto",
    author: "barb",
    author_kind: "staff",
    kind: "text",
    body_md: "Hello! I am Barb. How can I help you today?",
    delivery: "complete",
    created_at: "2026-09-25T12:00:00Z",
    seq: 1,
  },
  {
    id: "msg-prop",
    thread_id: "thread-barb-auto",
    author: "barb",
    author_kind: "staff",
    kind: "action_proposal",
    body_md: "Proposal for disk cleanup",
    delivery: "complete",
    created_at: "2026-09-25T12:01:00Z",
    seq: 2,
    meta: {
      proposal: {
        id: "prop-999",
        thread_id: "thread-barb-auto",
        message_id: "msg-prop",
        action: "maintenance.trim_worktrees",
        description: "Trim 12 stale git worktrees on ControlTower",
        risk_level: "medium",
        status: "proposed",
        params: { host: "ControlTower" },
        created_at: "2026-09-25T12:01:00Z",
      },
    },
  },
];

describe("StaffConsoleMobile (SC-D8)", () => {
  let originalLocation: Location;

  beforeEach(() => {
    originalLocation = window.location;
    // Mock window.location
    delete (window as unknown as { location?: Location }).location;
    window.location = {
      ...originalLocation,
      search: "",
      pathname: "/staff",
    } as Location;

    vi.spyOn(window.history, "pushState").mockImplementation(() => {});
  });

  afterEach(() => {
    window.location = originalLocation;
    vi.restoreAllMocks();
  });

  it("renders the mobile roster view by default with Ask Barb and role groups", () => {
    render(<StaffConsoleMobile roles={MOCK_ROLES} />);

    expect(screen.getByTestId("staff-mobile-roster")).toBeInTheDocument();
    expect(screen.getByTestId("staff-mobile-ask-barb")).toBeInTheDocument();
    expect(screen.getByText("Chief Architect")).toBeInTheDocument();
    expect(screen.getByText("Fleet Maintenance")).toBeInTheDocument();
  });

  it("filters roles in the mobile roster via search input", () => {
    render(<StaffConsoleMobile roles={MOCK_ROLES} />);

    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: "Architect" } });

    expect(screen.getByText("Chief Architect")).toBeInTheDocument();
    expect(screen.queryByText("Fleet Maintenance")).not.toBeInTheDocument();
  });

  it("navigates full-screen from roster to thread view when a role is tapped", async () => {
    const onOpenThread = vi.fn();
    render(
      <StaffConsoleMobile
        roles={MOCK_ROLES}
        initialThread={MOCK_BARB_THREAD}
        initialMessages={MOCK_MESSAGES}
        onOpenThread={onOpenThread}
      />,
    );

    const askBarbBtn = screen.getByTestId("staff-mobile-ask-barb");
    fireEvent.click(askBarbBtn);

    await waitFor(() => {
      expect(screen.getByTestId("staff-mobile-thread")).toBeInTheDocument();
      expect(screen.getByTestId("staff-mobile-back-btn")).toBeInTheDocument();
    });

    expect(screen.getByText("Conversation with Barb")).toBeInTheDocument();
    expect(screen.getByText("Hello! I am Barb. How can I help you today?")).toBeInTheDocument();
  });

  it("navigates back to the roster view when the back button is clicked", async () => {
    render(
      <StaffConsoleMobile
        roles={MOCK_ROLES}
        initialThread={MOCK_BARB_THREAD}
        initialMessages={MOCK_MESSAGES}
        initialView="thread"
      />,
    );

    expect(screen.getByTestId("staff-mobile-thread")).toBeInTheDocument();
    const backBtn = screen.getByTestId("staff-mobile-back-btn");
    fireEvent.click(backBtn);

    await waitFor(() => {
      expect(screen.getByTestId("staff-mobile-roster")).toBeInTheDocument();
    });
  });

  it("renders safe-area aware bottom composer in thread view and sends messages", async () => {
    const onSendMessage = vi.fn().mockResolvedValue({ ok: true });

    render(
      <StaffConsoleMobile
        roles={MOCK_ROLES}
        initialThread={MOCK_BARB_THREAD}
        initialMessages={MOCK_MESSAGES}
        initialView="thread"
        onSendMessage={onSendMessage}
      />,
    );

    const composerContainer = screen.getByTestId("staff-mobile-composer-container");
    expect(composerContainer).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText(/message/i);
    fireEvent.change(textarea, { target: { value: "Status report please" } });

    const sendBtn = screen.getByRole("button", { name: /send/i });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(onSendMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          body: "Status report please",
        }),
      );
    });
  });

  it("renders thumb-reachable Approve / Deny actions for action proposals", async () => {
    const onApproveProposal = vi.fn();
    const onDenyProposal = vi.fn();

    render(
      <StaffConsoleMobile
        roles={MOCK_ROLES}
        initialThread={MOCK_BARB_THREAD}
        initialMessages={MOCK_MESSAGES}
        initialView="thread"
        onApproveProposal={onApproveProposal}
        onDenyProposal={onDenyProposal}
      />,
    );

    const approveBtn = screen.getByRole("button", { name: /approve/i });
    const denyBtn = screen.getByRole("button", { name: /deny/i });

    expect(approveBtn).toBeInTheDocument();
    expect(denyBtn).toBeInTheDocument();

    fireEvent.click(approveBtn);
    expect(onApproveProposal).toHaveBeenCalledWith("prop-999", { host: "ControlTower" });
  });

  it("opens thread view directly when ?thread=<id> is present in the URL", () => {
    window.location.search = "?thread=thread-barb-auto";

    render(
      <StaffConsoleMobile
        roles={MOCK_ROLES}
        initialThread={MOCK_BARB_THREAD}
        initialMessages={MOCK_MESSAGES}
      />,
    );

    expect(screen.getByTestId("staff-mobile-thread")).toBeInTheDocument();
    expect(screen.getByText("Conversation with Barb")).toBeInTheDocument();
  });

  it("opens role thread directly when ?role=<role> is present in the URL", () => {
    window.location.search = "?role=architect";

    render(
      <StaffConsoleMobile
        roles={MOCK_ROLES}
        initialThread={{
          id: "thread-architect",
          title: "Chief Architect",
          kind: "direct",
          participants: ["user", "architect"],
          status: "active",
        }}
      />,
    );

    expect(screen.getByTestId("staff-mobile-thread")).toBeInTheDocument();
    expect(screen.getByText("Chief Architect")).toBeInTheDocument();
  });

  it("toggles the role context pane drawer from thread view", async () => {
    render(
      <StaffConsoleMobile
        roles={MOCK_ROLES}
        initialThread={MOCK_BARB_THREAD}
        initialMessages={MOCK_MESSAGES}
        initialView="thread"
      />,
    );

    const detailsBtn = screen.getByTestId("staff-mobile-details-btn");
    fireEvent.click(detailsBtn);

    await waitFor(() => {
      expect(screen.getByTestId("staff-mobile-context-drawer")).toBeInTheDocument();
    });

    const closeDrawerBtn = screen.getByTestId("staff-mobile-close-drawer");
    fireEvent.click(closeDrawerBtn);

    await waitFor(() => {
      expect(screen.queryByTestId("staff-mobile-context-drawer")).not.toBeInTheDocument();
    });
  });

  it("switches to Waiting on You inbox view via tab toggle", async () => {
    render(<StaffConsoleMobile roles={MOCK_ROLES} />);

    const inboxTab = screen.getByTestId("staff-mobile-tab-inbox");
    fireEvent.click(inboxTab);

    await waitFor(() => {
      expect(screen.getByTestId("staff-mobile-inbox-view")).toBeInTheDocument();
    });
  });
});
