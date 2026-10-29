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
import fs from "fs";
import path from "path";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
// Thread resolution hits the backend (#1446); keep it hermetic.
const threadApi = vi.hoisted(() => ({
  fetchThreads: vi.fn(),
  fetchThread: vi.fn(),
  fetchThreadMessages: vi.fn(),
  fetchRoster: vi.fn(),
  fetchRuns: vi.fn(),
  getActionProposal: vi.fn(),
  postThreadMessage: vi.fn(),
}));
vi.mock("../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../Staff/staffApi")>()),
  ...threadApi,
}));

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
    name: "rd-barb",
    title: "RD Barb",
    summary: "Persistent Runner Dashboard Barb seat",
    group: "leadership",
    valid: true,
    dispatchable: false,
    active_runs: 0,
    caller_unread_count: 0,
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

const MOCK_RD_BARB_THREAD: ThreadInfo = {
  id: "thread-rd-barb-direct",
  title: "Conversation with RD Barb",
  kind: "direct",
  participants: ["user", "rd-barb"],
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
        description: "Trim 12 stale git worktrees on node-a",
        risk_level: "medium",
        status: "proposed",
        params: { host: "node-a" },
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
    threadApi.fetchThreads.mockResolvedValue({ threads: [MOCK_BARB_THREAD, MOCK_RD_BARB_THREAD] });
    threadApi.fetchThreadMessages.mockResolvedValue({ messages: [] });
    threadApi.fetchRoster.mockResolvedValue({ roles: MOCK_ROLES });
    threadApi.fetchRuns.mockResolvedValue({
      runs: [
        {
          id: "run-test-1",
          role: "barb",
          provider: "claude",
          status: "running",
          repo: "D-sorganization/Runner_Dashboard",
          created_at: "2026-09-25T12:00:00Z",
          cost_usd: 0.42,
        },
      ],
      total: 1,
    });
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

  it("provides a chat-first home with keyboard project starters and no automatic send", async () => {
    const user = userEvent.setup();
    const onSendMessage = vi.fn().mockResolvedValue({ ok: true });
    render(<StaffConsoleMobile initialView="home" roles={MOCK_ROLES} onSendMessage={onSendMessage} />);

    expect(screen.getByTestId("staff-mobile-home")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose staff" })).toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: /staff conversation input/i });
    expect(input).toBeInTheDocument();

    const upstream = screen.getByRole("button", { name: "Start UpstreamDrift" });
    upstream.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toContain("UpstreamDrift"));
    expect((input as HTMLTextAreaElement).value).toContain("review open issues and pull requests in the UpstreamDrift repository");
    expect((input as HTMLTextAreaElement).value).toContain("wait for my approval before dispatch");
    expect(document.activeElement).toBe(input);
    expect(onSendMessage).not.toHaveBeenCalled();

    const affine = screen.getByRole("button", { name: "Start AffineDrift" });
    affine.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toContain("AffineDrift"));
    expect((input as HTMLTextAreaElement).value).toContain("review open issues and pull requests in the AffineDrift repository");
    expect(onSendMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Choose staff" }));
    expect(screen.getByTestId("staff-mobile-roster")).toBeInTheDocument();
  });

  it("opens rd-barb's direct conversation (not the auto-route barb thread) after a successful send from the chat-first home (#3493)", async () => {
    window.localStorage.clear();
    const user = userEvent.setup();
    threadApi.postThreadMessage.mockResolvedValue({ id: "sent-mobile-home" });
    render(<StaffConsoleMobile initialView="home" roles={MOCK_ROLES} />);

    expect(screen.getByPlaceholderText("Message RD Barb...")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: /staff conversation input/i }), "Check the queue");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(screen.getByTestId("staff-mobile-thread")).toBeInTheDocument());
    expect(screen.queryByTestId("staff-mobile-home")).not.toBeInTheDocument();
    expect(threadApi.postThreadMessage).toHaveBeenCalledWith(
      MOCK_RD_BARB_THREAD.id,
      expect.objectContaining({ body: "Check the queue" }),
      expect.any(String),
    );
    expect(threadApi.postThreadMessage).not.toHaveBeenCalledWith(
      MOCK_BARB_THREAD.id,
      expect.anything(),
      expect.anything(),
    );
    expect(window.history.pushState).toHaveBeenCalledWith(
      null,
      "",
      "?role=rd-barb&thread=thread-rd-barb-direct",
    );
  });

  it("locks the chat-first home and shows an explicit status instead of falling back to barb when rd-barb is missing from the roster (#3493)", async () => {
    const rolesWithoutRdBarb = MOCK_ROLES.filter((role) => role.name !== "rd-barb");
    const callsBefore = threadApi.postThreadMessage.mock.calls.length;
    render(<StaffConsoleMobile initialView="home" roles={rolesWithoutRdBarb} />);

    expect(screen.getByTestId("staff-mobile-rd-barb-unavailable")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /staff conversation input/i })).toBeDisabled();
    expect(threadApi.postThreadMessage.mock.calls.length).toBe(callsBefore);
  });

  it("does not reopen the thread when the user leaves home before a send completes", async () => {
    window.localStorage.clear();
    const user = userEvent.setup();
    let resolvePost!: (message: { id: string }) => void;
    threadApi.postThreadMessage.mockReturnValue(new Promise((resolve) => { resolvePost = resolve; }));
    render(<StaffConsoleMobile initialView="home" roles={MOCK_ROLES} />);

    await user.type(screen.getByRole("textbox", { name: /staff conversation input/i }), "Check the queue");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await user.click(screen.getByRole("button", { name: "Choose staff" }));
    expect(screen.getByTestId("staff-mobile-roster")).toBeInTheDocument();

    await act(async () => { resolvePost({ id: "sent-after-navigation" }); });

    expect(screen.getByTestId("staff-mobile-roster")).toBeInTheDocument();
    expect(screen.queryByTestId("staff-mobile-thread")).not.toBeInTheDocument();
  });

  it("keeps the mobile home and draft when the first send fails", async () => {
    window.localStorage.clear();
    const user = userEvent.setup();
    threadApi.postThreadMessage.mockRejectedValue(new Error("network unavailable"));
    render(<StaffConsoleMobile initialView="home" roles={MOCK_ROLES} />);

    const input = screen.getByRole("textbox", { name: /staff conversation input/i });
    await user.type(input, "Try again");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(screen.getByTestId("staff-mobile-home")).toBeInTheDocument());
    expect(screen.queryByTestId("staff-mobile-thread")).not.toBeInTheDocument();
    expect(input).toHaveValue("Try again");
  });

  it("offers project starters in the mobile roster and only pre-fills the Barb composer", async () => {
    const onSendMessage = vi.fn().mockResolvedValue({ ok: true });
    render(<StaffConsoleMobile roles={MOCK_ROLES} onSendMessage={onSendMessage} />);

    fireEvent.click(screen.getByRole("button", { name: "Start UpstreamDrift" }));

    await waitFor(() => {
      expect(screen.getByTestId("staff-mobile-thread")).toBeInTheDocument();
    });
    await waitFor(() => expect(screen.getByPlaceholderText(/message/i).value).toContain(
      "Triage UpstreamDrift",
    ));
    expect(onSendMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("staff-mobile-back-btn"));
    fireEvent.click(screen.getByRole("button", { name: "Start AffineDrift" }));
    await waitFor(() => expect(screen.getByPlaceholderText(/message/i).value).toContain(
      "Triage AffineDrift",
    ));
    expect(onSendMessage).not.toHaveBeenCalled();
  });

  it("lists roles whose API group is free-form or missing (desktop tiering)", () => {
    const liveShaped: StaffRoleItem[] = [
      { ...MOCK_ROLES[1], name: "librarian", title: "Librarian", group: "Specialist" },
      { ...MOCK_ROLES[1], name: "project-steward", title: "Project Steward", group: undefined },
    ];
    render(<StaffConsoleMobile roles={liveShaped} />);

    expect(screen.getByText("Librarian")).toBeInTheDocument();
    expect(screen.getByText("Project Steward")).toBeInTheDocument();
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

  it("opens the role's real backend thread, not an invented id", async () => {
    const onOpenThread = vi.fn();
    render(<StaffConsoleMobile roles={MOCK_ROLES} onOpenThread={onOpenThread} />);

    fireEvent.click(screen.getByTestId("staff-mobile-ask-barb"));

    await waitFor(() => expect(onOpenThread).toHaveBeenCalledWith("thread-barb-auto"));
    expect(threadApi.fetchThreads).toHaveBeenCalledWith({ role: "barb" });
  });

  it("stays on the roster and shows an alert when the thread cannot be opened", async () => {
    threadApi.fetchThreads.mockRejectedValue(new Error("conversations unavailable"));
    render(<StaffConsoleMobile roles={MOCK_ROLES} />);

    fireEvent.click(screen.getByTestId("staff-mobile-role-architect"));

    expect(await screen.findByRole("alert")).toHaveTextContent("conversations unavailable");
    expect(screen.getByTestId("staff-mobile-roster")).toBeInTheDocument();
    expect(screen.queryByTestId("staff-mobile-thread")).not.toBeInTheDocument();
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
    threadApi.getActionProposal.mockResolvedValue({ id: "x", can_approve: true, denial_reason: null, params_hash: "e".repeat(64) });
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

    await waitFor(() => expect(approveBtn).toBeEnabled());
    fireEvent.click(approveBtn);
    expect(onApproveProposal).toHaveBeenCalledWith("prop-999", { host: "node-a" }, "e".repeat(64));
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

  it("shows an empty-thread hint instead of a blank pane when there are no messages", () => {
    window.location.search = "?thread=thread-barb-auto";
    render(<StaffConsoleMobile roles={MOCK_ROLES} initialThread={MOCK_BARB_THREAD} initialMessages={[]} />);

    const empty = screen.getByTestId("staff-mobile-thread-empty");
    expect(empty).toHaveTextContent(/No messages yet/i);
  });

  it("hides the empty-thread hint once the thread has messages", () => {
    window.location.search = "?thread=thread-barb-auto";
    render(
      <StaffConsoleMobile roles={MOCK_ROLES} initialThread={MOCK_BARB_THREAD} initialMessages={MOCK_MESSAGES} />,
    );

    expect(screen.queryByTestId("staff-mobile-thread-empty")).not.toBeInTheDocument();
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

  it("switches to Runs view via tab toggle and displays runs", async () => {
    render(<StaffConsoleMobile roles={MOCK_ROLES} />);

    const runsTab = screen.getByTestId("staff-mobile-tab-runs");
    fireEvent.click(runsTab);

    await waitFor(() => {
      expect(screen.getByTestId("staff-mobile-runs-view")).toBeInTheDocument();
      expect(screen.getByTestId("staff-mobile-run-run-test-1")).toBeInTheDocument();
    });
  });

  it("renders Ask Barb without emoji in UI chrome", () => {
    render(<StaffConsoleMobile roles={MOCK_ROLES} />);
    const askBarb = screen.getByTestId("staff-mobile-ask-barb");
    expect(askBarb.textContent).not.toContain("🤖");
    expect(askBarb.querySelector("svg")).toBeInTheDocument();
  });

  it("opens runs view directly when ?tab=runs is present in URL", async () => {
    window.location.search = "?tab=runs";
    render(<StaffConsoleMobile roles={MOCK_ROLES} />);

    await waitFor(() => {
      expect(screen.getByTestId("staff-mobile-runs-view")).toBeInTheDocument();
    });
  });

  it("enforces 44px+ touch targets in mobile.css contract", () => {
    const css = fs.readFileSync(path.join(__dirname, "../mobile.css"), "utf8");

    expect(css).toMatch(/\.staff-mobile__tab\s*\{[^}]*min-height:\s*44px/);
    expect(css).toMatch(/\.staff-action-card__btn\s*\{[^}]*min-height:\s*44px/);
    expect(css).toMatch(/env\(safe-area-inset-bottom/);
  });

  // #2663: the mobile console must follow a Barb thread selected from the
  // shell's sidebar (same onThreadSelected contract as #2652's Desktop fix)
  // and fail closed — never send to a stale thread — while the newly
  // selected id is still being fetched and authorized, or if that fails.
  describe("route-selected thread sync and fail-closed composer lock (#2663)", () => {
    const THREAD_B: ThreadInfo = {
      id: "thread-barb-b",
      title: "New Barb Mission",
      kind: "auto",
      participants: ["user", "barb"],
      status: "active",
    };
    const THREAD_MAINT: ThreadInfo = {
      id: "thread-maint",
      title: "Fleet Maintenance",
      kind: "direct",
      participants: ["user", "maintenance"],
      status: "active",
    };

    function renderOnThreadA() {
      return render(
        <StaffConsoleMobile
          roles={MOCK_ROLES}
          initialThread={MOCK_BARB_THREAD}
          initialMessages={MOCK_MESSAGES}
          initialView="thread"
        />,
      );
    }

    it("mobile console follows sidebar Barb selection", async () => {
      const { rerender } = renderOnThreadA();
      expect(screen.getByText("Conversation with Barb")).toBeInTheDocument();

      threadApi.fetchThread.mockResolvedValue({ thread: THREAD_B, messages: [] });
      window.location.search = "?role=barb&thread=thread-barb-b";
      rerender(
        <StaffConsoleMobile
          roles={MOCK_ROLES}
          initialThread={MOCK_BARB_THREAD}
          initialMessages={MOCK_MESSAGES}
          initialView="thread"
        />,
      );

      await waitFor(() => expect(screen.getByText("New Barb Mission")).toBeInTheDocument());
      expect(threadApi.fetchThread).toHaveBeenCalledWith("thread-barb-b", expect.anything());
    });

    it("mobile composer cannot send or stage attachments while selected thread is pending or failed", async () => {
      let resolveFetch!: (value: { thread: ThreadInfo; messages: ThreadMessage[] }) => void;
      threadApi.fetchThread.mockReturnValue(
        new Promise((resolve) => { resolveFetch = resolve; }),
      );

      const { rerender } = renderOnThreadA();
      window.location.search = "?role=barb&thread=thread-barb-b";
      rerender(
        <StaffConsoleMobile
          roles={MOCK_ROLES}
          initialThread={MOCK_BARB_THREAD}
          initialMessages={MOCK_MESSAGES}
          initialView="thread"
        />,
      );

      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(/opening a conversation/i),
      );
      expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
      expect(screen.getByTestId("composer-attach-btn")).toBeDisabled();

      await act(async () => { resolveFetch({ thread: THREAD_B, messages: [] }); });

      await waitFor(() =>
        expect(screen.getByTestId("composer-attach-btn")).not.toBeDisabled(),
      );
    });

    it("dismissing a load error does not unlock the previous thread", async () => {
      threadApi.fetchThread.mockRejectedValue(new Error("not authorized for this thread"));

      const { rerender } = renderOnThreadA();
      window.location.search = "?role=barb&thread=thread-barb-b";
      rerender(
        <StaffConsoleMobile
          roles={MOCK_ROLES}
          initialThread={MOCK_BARB_THREAD}
          initialMessages={MOCK_MESSAGES}
          initialView="thread"
        />,
      );

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(/not authorized for this thread/i);
      expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();

      fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));

      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByText("Conversation with Barb")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    });

    it("specialist routing stays isolated from sidebar selection; drafts stay with their thread", async () => {
      threadApi.fetchThreads.mockResolvedValue({ threads: [MOCK_BARB_THREAD, THREAD_MAINT] });
      renderOnThreadA();
      expect(screen.getByText("Conversation with Barb")).toBeInTheDocument();
      expect(screen.getByTestId("composer-attach-btn")).not.toBeDisabled();

      fireEvent.click(screen.getByTestId("staff-mobile-back-btn"));
      fireEvent.click(screen.getByTestId("staff-mobile-role-maintenance"));

      await waitFor(() => expect(screen.getByText("Fleet Maintenance")).toBeInTheDocument());
      // A stale Barb route id from the first thread must not lock the
      // composer once the console has locally, and successfully, switched
      // to a specialist's own authorized thread.
      expect(screen.getByTestId("composer-attach-btn")).not.toBeDisabled();
    });
  });
});
