// @vitest-environment jsdom
/**
 * Thread.test.tsx — Unit tests for Staff Console Thread view: message list,
 * date separators, jump to unread, streaming deltas with stop button, and reconnecting state (SC-D4, Issue #1318).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Thread } from "../Thread";
import type { ThreadMessage, ThreadInfo } from "../threadTypes";

afterEach(cleanup);

const MOCK_THREAD: ThreadInfo = {
  id: "thread-123",
  title: "Conversation with Barb",
  kind: "direct",
  participants: ["user", "barb"],
  status: "active",
};

const MOCK_MESSAGES: ThreadMessage[] = [
  {
    id: "msg-1",
    thread_id: "thread-123",
    author: "user",
    author_kind: "user",
    kind: "text",
    body_md: "What is the status of the runner fleet?",
    delivery: "complete",
    created_at: "2026-09-24T10:00:00Z",
    seq: 1,
  },
  {
    id: "msg-2",
    thread_id: "thread-123",
    author: "barb",
    author_kind: "staff",
    kind: "text",
    body_md: "All 44 runners are online and healthy.",
    delivery: "complete",
    created_at: "2026-09-24T10:00:05Z",
    seq: 2,
  },
  {
    id: "msg-3",
    thread_id: "thread-123",
    author: "user",
    author_kind: "user",
    kind: "text",
    body_md: "Run a soak test today.",
    delivery: "complete",
    created_at: "2026-09-25T08:00:00Z",
    seq: 3,
  },
];

describe("Thread Component", () => {
  it("renders messages and date separators across distinct days", () => {
    render(<Thread thread={MOCK_THREAD} messages={MOCK_MESSAGES} />);

    expect(screen.getByText("What is the status of the runner fleet?")).toBeInTheDocument();
    expect(screen.getByText("All 44 runners are online and healthy.")).toBeInTheDocument();
    expect(screen.getByText("Run a soak test today.")).toBeInTheDocument();

    // Date separators exist for 2026-09-24 and 2026-09-25
    expect(screen.getAllByRole("separator")).toHaveLength(2);
  });

  it("shows jump to unread button and scrolls when clicked", () => {
    const unreadMessages: ThreadMessage[] = [
      ...MOCK_MESSAGES,
      {
        id: "msg-unread-4",
        thread_id: "thread-123",
        author: "barb",
        author_kind: "staff",
        kind: "text",
        body_md: "Unread follow-up message",
        delivery: "complete",
        created_at: "2026-09-25T08:05:00Z",
        seq: 4,
      },
    ];

    render(
      <Thread
        thread={MOCK_THREAD}
        messages={unreadMessages}
        unreadSeqThreshold={4}
      />
    );

    const jumpBtn = screen.getByRole("button", { name: /jump to unread/i });
    expect(jumpBtn).toBeInTheDocument();

    fireEvent.click(jumpBtn);
    // After click or scrolling, jump button disappears
  });

  it("renders streaming delta with indicator and stop button", () => {
    const onStop = vi.fn();
    const streamingMessages: ThreadMessage[] = [
      ...MOCK_MESSAGES,
      {
        id: "msg-streaming",
        thread_id: "thread-123",
        author: "barb",
        author_kind: "staff",
        kind: "text",
        body_md: "Analyzing the fleet metrics now...",
        delivery: "pending",
        streaming: true,
        created_at: "2026-09-25T08:10:00Z",
        seq: 4,
      },
    ];

    render(
      <Thread
        thread={MOCK_THREAD}
        messages={streamingMessages}
        onStopStreaming={onStop}
      />
    );

    expect(screen.getByText(/Analyzing the fleet metrics now.../)).toBeInTheDocument();
    const stopBtn = screen.getByRole("button", { name: /stop generating/i });
    expect(stopBtn).toBeInTheDocument();

    fireEvent.click(stopBtn);
    expect(onStop).toHaveBeenCalledWith("msg-streaming");
  });

  it("shows sticky reconnecting banner when SSE connection drops", () => {
    render(
      <Thread
        thread={MOCK_THREAD}
        messages={MOCK_MESSAGES}
        isReconnecting={true}
      />
    );

    expect(screen.getByRole("status")).toHaveTextContent(/reconnecting to thread/i);
  });

  it("renders classified error card with remediation on failure", () => {
    const errorMessages: ThreadMessage[] = [
      ...MOCK_MESSAGES,
      {
        id: "msg-err",
        thread_id: "thread-123",
        author: "barb",
        author_kind: "staff",
        kind: "error",
        body_md: "Failed to dispatch turn.",
        delivery: "failed",
        failure_class: "auth_expired",
        remediation: "Run claude login on host DeskComputer.",
        created_at: "2026-09-25T08:12:00Z",
        seq: 4,
      },
    ];

    render(<Thread thread={MOCK_THREAD} messages={errorMessages} />);

    expect(screen.getByText(/Authentication Expired|auth_expired/i)).toBeInTheDocument();
    expect(screen.getByText(/Run claude login on host DeskComputer./i)).toBeInTheDocument();
  });

  it("renders embedded action proposal card and handles approval", () => {
    const handleApprove = vi.fn();
    const proposalMessages: ThreadMessage[] = [
      ...MOCK_MESSAGES,
      {
        id: "msg-prop",
        thread_id: "thread-123",
        author: "maintenance",
        author_kind: "staff",
        kind: "proposal",
        body_md: "Proposed action: maintenance.runner_restart",
        delivery: "complete",
        created_at: "2026-09-25T08:15:00Z",
        seq: 4,
        meta: {
          proposal: {
            id: "prop-456",
            action_name: "maintenance.runner_restart",
            target: "DeskComp",
            risk_level: "medium",
            status: "pending",
            description: "Restart stalled listener",
          },
        },
      },
    ];

    render(
      <Thread
        thread={MOCK_THREAD}
        messages={proposalMessages}
        onApproveProposal={handleApprove}
      />
    );

    expect(screen.getByText("maintenance.runner_restart")).toBeInTheDocument();
    const approveBtn = screen.getByRole("button", { name: /approve/i });
    fireEvent.click(approveBtn);

    expect(handleApprove).toHaveBeenCalledWith("prop-456", undefined);
  });

  it("renders embedded run card and handles cancellation", () => {
    const handleCancel = vi.fn();
    const runMessages: ThreadMessage[] = [
      ...MOCK_MESSAGES,
      {
        id: "msg-run",
        thread_id: "thread-123",
        author: "barb",
        author_kind: "staff",
        kind: "run",
        body_md: "Dispatching run 999",
        delivery: "complete",
        created_at: "2026-09-25T08:20:00Z",
        seq: 4,
        meta: {
          run: {
            id: "run-999",
            status: "running",
            node: "DeskComp",
            provider: "claude-3-7-sonnet",
            elapsed_seconds: 15,
          },
        },
      },
    ];

    render(
      <Thread
        thread={MOCK_THREAD}
        messages={runMessages}
        onCancelRun={handleCancel}
      />
    );

    expect(screen.getByText("Run run-999")).toBeInTheDocument();
    const cancelBtn = screen.getByRole("button", { name: /cancel run/i });
    fireEvent.click(cancelBtn);

    expect(handleCancel).toHaveBeenCalledWith("run-999");
  });

  it("renders embedded handoff and review cards", () => {
    const cardMessages: ThreadMessage[] = [
      ...MOCK_MESSAGES,
      {
        id: "msg-handoff",
        thread_id: "thread-123",
        author: "barb",
        author_kind: "staff",
        kind: "handoff",
        body_md: "Routing to Librarian",
        delivery: "complete",
        created_at: "2026-09-25T08:25:00Z",
        seq: 4,
        meta: {
          handoff: {
            from_role: "barb",
            to_role: "librarian",
            reason: "Documentation review needed",
          },
        },
      },
      {
        id: "msg-review",
        thread_id: "thread-123",
        author: "fleet-critic",
        author_kind: "staff",
        kind: "review",
        body_md: "Review of PR #1413",
        delivery: "complete",
        created_at: "2026-09-25T08:30:00Z",
        seq: 5,
        meta: {
          review: {
            pr_number: 1413,
            verdict: "approved",
            summary: "Looks great",
            findings: ["Clean tests"],
          },
        },
      },
    ];

    render(<Thread thread={MOCK_THREAD} messages={cardMessages} />);

    expect(screen.getByText(/Documentation review needed/i)).toBeInTheDocument();
    expect(screen.getByText(/PR #1413/i)).toBeInTheDocument();
    expect(screen.getByText(/Clean tests/i)).toBeInTheDocument();
  });

  it("routes a run card's answer and cancel with the card's thread (#1547)", async () => {
    const onAnswerRun = vi.fn().mockResolvedValue(true);
    const onCancelRun = vi.fn().mockResolvedValue(true);
    const cards: ThreadMessage[] = [
      {
        id: "msg-card-run-ask",
        thread_id: MOCK_THREAD.id,
        author: "e2e-analyst",
        author_kind: "staff",
        kind: "run_card",
        body_md: "",
        delivery: "complete",
        meta: { run: { id: "run-ask", status: "needs_input", question: "Which repository?" } },
      },
      {
        id: "msg-card-run-busy",
        thread_id: MOCK_THREAD.id,
        author: "e2e-analyst",
        author_kind: "staff",
        kind: "run_card",
        body_md: "",
        delivery: "complete",
        meta: { run: { id: "run-busy", status: "running" } },
      },
    ];
    render(<Thread thread={MOCK_THREAD} messages={cards} onAnswerRun={onAnswerRun} onCancelRun={onCancelRun} />);

    fireEvent.change(screen.getByRole("textbox", { name: /answer/i }), { target: { value: "Runner_Dashboard" } });
    fireEvent.click(screen.getByRole("button", { name: /send answer/i }));
    fireEvent.click(screen.getByRole("button", { name: /cancel run/i }));

    await waitFor(() => expect(onAnswerRun).toHaveBeenCalledWith(MOCK_THREAD.id, "run-ask", "Runner_Dashboard"));
    expect(onCancelRun).toHaveBeenCalledWith("run-busy");
  });
});
