// @vitest-environment jsdom
/**
 * useThreadStream.test.ts — Unit tests for useThreadStream hook (SC-D4, Issue #1318).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useThreadStream } from "../useThreadStream";
import type { ThreadMessage } from "../threadTypes";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((ev: MessageEvent) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
    setTimeout(() => {
      if (this.onopen) this.onopen();
    }, 0);
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }

  emit(type: string, data: unknown, lastEventId?: string) {
    const ev = {
      data: typeof data === "string" ? data : JSON.stringify(data),
      lastEventId: lastEventId || "",
    } as MessageEvent;

    if (this.listeners[type]) {
      for (const fn of this.listeners[type]) {
        fn(ev);
      }
    }
  }

  close = vi.fn();
}

const INITIAL_MESSAGES: ThreadMessage[] = [
  {
    id: "msg-1",
    thread_id: "thread-abc",
    author: "user",
    author_kind: "user",
    kind: "text",
    body_md: "Hello",
    delivery: "complete",
    seq: 1,
  },
];

describe("useThreadStream", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    // @ts-expect-error Mocking global EventSource
    globalThis.EventSource = MockEventSource;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("initializes with provided initialMessages and connects to stream", () => {
    const { result } = renderHook(() =>
      useThreadStream({ threadId: "thread-abc", initialMessages: INITIAL_MESSAGES })
    );

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].body_md).toBe("Hello");
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain("/api/v1/staff/threads/thread-abc/stream?since_seq=1&follow=true");
  });

  it("appends optimistic message and updates message", () => {
    const { result } = renderHook(() =>
      useThreadStream({ threadId: "thread-abc", initialMessages: INITIAL_MESSAGES })
    );

    const newMsg: ThreadMessage = {
      id: "msg-opt-2",
      thread_id: "thread-abc",
      author: "user",
      author_kind: "user",
      kind: "text",
      body_md: "Optimistic message",
      delivery: "pending",
    };

    act(() => {
      result.current.appendOptimisticMessage(newMsg);
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].id).toBe("msg-opt-2");

    act(() => {
      result.current.updateMessage("msg-opt-2", (prev) => ({
        ...prev,
        delivery: "complete",
      }));
    });

    expect(result.current.messages[1].delivery).toBe("complete");
  });

  it("aggregates token deltas and allows stopping streaming", () => {
    const { result } = renderHook(() =>
      useThreadStream({ threadId: "thread-abc", initialMessages: INITIAL_MESSAGES })
    );

    const es = MockEventSource.instances[0];

    // Emit token event
    act(() => {
      es.emit("token", { message_id: "msg-stream-3", delta: "Processing" });
    });

    expect(result.current.activeStreamingId).toBe("msg-stream-3");
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].body_md).toBe("Processing");
    expect(result.current.messages[1].streaming).toBe(true);

    // Emit second token event delta
    act(() => {
      es.emit("token", { message_id: "msg-stream-3", delta: " request..." });
    });

    expect(result.current.messages[1].body_md).toBe("Processing request...");

    // Stop streaming
    act(() => {
      result.current.stopStreaming("msg-stream-3");
    });

    expect(result.current.activeStreamingId).toBeNull();
    expect(result.current.messages[1].streaming).toBe(false);
    expect(result.current.messages[1].delivery).toBe("complete");
  });

  it("sets reconnecting state on error and reconnects", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() =>
      useThreadStream({ threadId: "thread-abc", initialMessages: INITIAL_MESSAGES })
    );

    const es = MockEventSource.instances[0];

    act(() => {
      if (es.onerror) es.onerror();
    });

    expect(result.current.isReconnecting).toBe(true);
    expect(es.close).toHaveBeenCalled();

    // Fast-forward backoff timer
    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(MockEventSource.instances).toHaveLength(2);
    vi.useRealTimers();
  });

  // Replay frames carry the bare message; live frames wrap it as {message} (thread_bus.publish_message).
  // Treating the wrapper as a message appended an author-less entry and crashed the tab (#1341).
  const REPLY: ThreadMessage = {
    id: "msg-2",
    thread_id: "thread-abc",
    author: "e2e-analyst",
    author_kind: "role",
    kind: "text",
    body_md: "Fake reply",
    delivery: "complete",
    seq: 2,
  };

  it("applies a live {message} frame as the message it wraps", () => {
    const { result } = renderHook(() =>
      useThreadStream({ threadId: "thread-abc", initialMessages: INITIAL_MESSAGES })
    );

    act(() => {
      MockEventSource.instances[0].emit("message", { message: REPLY }, "2");
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1]).toMatchObject({ id: "msg-2", author: "e2e-analyst", body_md: "Fake reply" });
  });

  it("applies a bare replay frame", () => {
    const { result } = renderHook(() =>
      useThreadStream({ threadId: "thread-abc", initialMessages: INITIAL_MESSAGES })
    );

    act(() => {
      MockEventSource.instances[0].emit("message", REPLY, "2");
    });

    expect(result.current.messages[1]).toMatchObject({ id: "msg-2", author: "e2e-analyst" });
  });

  it("ignores a frame that is not a message", () => {
    const { result } = renderHook(() =>
      useThreadStream({ threadId: "thread-abc", initialMessages: INITIAL_MESSAGES })
    );

    act(() => {
      MockEventSource.instances[0].emit("message", { proposal: { id: "p1" } });
    });

    expect(result.current.messages).toHaveLength(1);
  });
});
