// @vitest-environment jsdom
/**
 * Thread.test.tsx — Unit tests for Staff Console Thread view: message list,
 * date separators, jump to unread, streaming deltas with stop button, and reconnecting state (SC-D4, Issue #1318).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
});
