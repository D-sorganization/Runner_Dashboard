import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { ThreadView } from "../ThreadView";
import type { ThreadMessage } from "../threadTypes";

const mockMessages: ThreadMessage[] = [
  {
    id: "m1",
    thread_id: "th_1",
    author_kind: "user",
    author: "alice",
    body_md: "Please check the fleet status",
    created_at: "2026-09-24T10:00:00Z",
    delivery: "complete",
    kind: "text",
  },
  {
    id: "m2",
    thread_id: "th_1",
    author_kind: "role",
    author: "barb",
    body_md: "All runners are currently operational.",
    created_at: "2026-09-24T10:00:02Z",
    delivery: "complete",
    kind: "text",
  },
  {
    id: "m3",
    thread_id: "th_1",
    author_kind: "user",
    author: "alice",
    body_md: "Morning Barb, any issues today?",
    created_at: "2026-09-25T08:00:00Z",
    delivery: "complete",
    kind: "text",
  },
];

describe("ThreadView (SC-D4)", () => {
  it("renders messages list with user messages aligned right and role messages aligned left", () => {
    const { container } = render(
      <ThreadView
        threadId="th_1"
        roleName="barb"
        roleTitle="Front Door & Triage"
        messages={mockMessages}
        onSendMessage={vi.fn()}
      />
    );

    expect(screen.getByText("Please check the fleet status")).not.toBeNull();
    expect(screen.getByText("All runners are currently operational.")).not.toBeNull();

    const userBubble = container.querySelector(".staff-message-item--user");
    const roleBubble = container.querySelector(".staff-message-item--role");
    expect(userBubble).not.toBeNull();
    expect(roleBubble).not.toBeNull();
  });

  it("inserts date separators across multiple days", () => {
    render(
      <ThreadView
        threadId="th_1"
        roleName="barb"
        messages={mockMessages}
        onSendMessage={vi.fn()}
      />
    );

    const separators = screen.getAllByRole("separator");
    expect(separators.length).toBeGreaterThanOrEqual(2);
  });

  it("renders reconnecting banner when SSE stream is reconnecting", () => {
    render(
      <ThreadView
        threadId="th_1"
        roleName="barb"
        messages={mockMessages}
        isReconnecting={true}
        onSendMessage={vi.fn()}
      />
    );

    expect(screen.getByText(/reconnecting to stream/i)).not.toBeNull();
  });

  it("renders streaming indicator with stop button when streaming is active", () => {
    const handleStop = vi.fn();
    render(
      <ThreadView
        threadId="th_1"
        roleName="barb"
        messages={mockMessages}
        isStreaming={true}
        onStopStreaming={handleStop}
        onSendMessage={vi.fn()}
      />
    );

    const stopButton = screen.getByRole("button", { name: /stop generating/i });
    expect(stopButton).not.toBeNull();

    fireEvent.click(stopButton);
    expect(handleStop).toHaveBeenCalledTimes(1);
  });

  it("renders error card and retry button when message fails delivery", () => {
    const failedMessages: ThreadMessage[] = [
      {
        id: "m_fail",
        thread_id: "th_1",
        author_kind: "role",
        author: "barb",
        body_md: "LLM provider timeout",
        created_at: "2026-09-25T08:05:00Z",
        delivery: "failed",
        kind: "error",
        meta: {
          failure_class: "timeout",
          idempotency_key: "idem_key_123",
        },
      },
    ];

    const handleRetry = vi.fn();
    render(
      <ThreadView
        threadId="th_1"
        roleName="barb"
        messages={failedMessages}
        onRetryMessage={handleRetry}
        onSendMessage={vi.fn()}
      />
    );

    expect(screen.getByText("LLM provider timeout")).not.toBeNull();
    const retryBtn = screen.getByRole("button", { name: /retry/i });
    expect(retryBtn).not.toBeNull();

    fireEvent.click(retryBtn);
    expect(handleRetry).toHaveBeenCalledWith("m_fail", "idem_key_123");
  });
});
