/**
 * useThreadStream.ts — SSE hook for live Staff Console conversation events:
 * token deltas, message completions, reconnection with Last-Event-ID, and manual stream abort.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ThreadMessage } from "./threadTypes";
import { dispatchDashboardNavigation } from "./dashboardNavigation";

export interface UseThreadStreamOptions {
  threadId: string;
  initialMessages?: ThreadMessage[];
  apiBase?: string;
  enabled?: boolean;
}

export interface UseThreadStreamResult {
  messages: ThreadMessage[];
  isReconnecting: boolean;
  activeStreamingId: string | null;
  stopStreaming: (messageId?: string) => void;
  appendOptimisticMessage: (msg: ThreadMessage) => void;
  updateMessage: (messageId: string, updater: (prev: ThreadMessage) => ThreadMessage) => void;
}

const NO_MESSAGES: ThreadMessage[] = [];

/**
 * The message a `message` SSE frame carries, or null when it carries none.
 *
 * Replay frames hold the bare message; live frames wrap it as `{message}`
 * (backend `thread_bus.publish_message`). Post: a returned message has a
 * string `id` and `author`, so rendering it cannot throw (#1341).
 */
export function messageFromFrame(data: unknown): ThreadMessage | null {
  if (!data || typeof data !== "object") return null;
  const wrapped = (data as { message?: unknown }).message;
  const candidate = wrapped && typeof wrapped === "object" ? wrapped : data;
  const { id, author } = candidate as { id?: unknown; author?: unknown };
  return typeof id === "string" && typeof author === "string" ? (candidate as ThreadMessage) : null;
}

export function useThreadStream({
  threadId,
  initialMessages = NO_MESSAGES,
  apiBase = "/api/v1/staff",
  enabled = true,
}: UseThreadStreamOptions): UseThreadStreamResult {
  const [messages, setMessages] = useState<ThreadMessage[]>(() => initialMessages.filter((message) => message.thread_id === threadId));
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [activeStreamingId, setActiveStreamingId] = useState<string | null>(null);

  const lastEventIdRef = useRef<number>(0);
  const messagesThreadRef = useRef(threadId);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingTokenDeltasRef = useRef(new Map<string, string>());
  const tokenFrameRef = useRef<number | null>(null);
  const tokenTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushTokenDeltas = useCallback(() => {
    if (tokenFrameRef.current !== null) {
      cancelAnimationFrame(tokenFrameRef.current);
      tokenFrameRef.current = null;
    }
    if (tokenTimeoutRef.current !== null) {
      clearTimeout(tokenTimeoutRef.current);
      tokenTimeoutRef.current = null;
    }

    const pending = pendingTokenDeltasRef.current;
    if (pending.size === 0) return;
    pendingTokenDeltasRef.current = new Map();
    setMessages((current) => {
      const next = [...current];
      const indexes = new Map(next.map((message, index) => [message.id, index]));
      for (const [messageId, delta] of pending) {
        const index = indexes.get(messageId);
        if (index === undefined) {
          indexes.set(messageId, next.length);
          next.push({
            id: messageId,
            thread_id: threadId,
            author: "staff",
            author_kind: "staff",
            kind: "text",
            body_md: delta,
            delivery: "pending",
            streaming: true,
          });
        } else {
          next[index] = {
            ...next[index],
            body_md: (next[index].body_md || "") + delta,
            streaming: true,
          };
        }
      }
      return next;
    });
  }, [threadId]);

  const scheduleTokenFlush = useCallback(() => {
    if (tokenFrameRef.current !== null || tokenTimeoutRef.current !== null) return;
    if (typeof requestAnimationFrame === "function") {
      tokenFrameRef.current = requestAnimationFrame(() => {
        tokenFrameRef.current = null;
        flushTokenDeltas();
      });
    } else {
      tokenTimeoutRef.current = setTimeout(() => {
        tokenTimeoutRef.current = null;
        flushTokenDeltas();
      }, 16);
    }
  }, [flushTokenDeltas]);

  // Delayed history may predate live events. Reset only for a different thread.
  useEffect(() => {
    const sameThread = messagesThreadRef.current === threadId;
    messagesThreadRef.current = threadId;
    const history = initialMessages.filter((message) => message.thread_id === threadId);
    setMessages((current) => {
      if (!sameThread) return history;
      const combined = new Map(history.map((message) => [message.id, message]));
      for (const message of current) {
        const historical = combined.get(message.id);
        combined.set(
          message.id,
          message.delivery === "pending" && historical && historical.delivery !== "pending"
            ? { ...message, ...historical, streaming: false }
            : message,
        );
      }
      return [...combined.values()].sort((a, b) => (a.seq ?? Number.MAX_SAFE_INTEGER) - (b.seq ?? Number.MAX_SAFE_INTEGER));
    });
    let maxSeq = sameThread ? lastEventIdRef.current : 0;
    for (const m of history) {
      if (m.seq && m.seq > maxSeq) {
        maxSeq = m.seq;
      }
    }
    lastEventIdRef.current = maxSeq;
    setActiveStreamingId((current) =>
      !sameThread || history.some((message) => message.id === current && message.delivery !== "pending") ? null : current,
    );
  }, [threadId, initialMessages]);

  const stopStreaming = useCallback((messageId?: string) => {
    flushTokenDeltas();
    setMessages((prev) =>
      prev.map((m) => {
        if (!messageId || m.id === messageId) {
          return { ...m, streaming: false, delivery: "complete" };
        }
        return m;
      })
    );
    setActiveStreamingId(null);
  }, [flushTokenDeltas]);

  const appendOptimisticMessage = useCallback((msg: ThreadMessage) => {
    setMessages((prev) => {
      // Check if message already exists by id
      const exists = prev.some((m) => m.id === msg.id);
      if (exists) {
        return prev.map((m) => (m.id === msg.id ? { ...m, ...msg } : m));
      }
      return [...prev, msg];
    });
  }, []);

  const updateMessage = useCallback(
    (messageId: string, updater: (prev: ThreadMessage) => ThreadMessage) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? updater(m) : m))
      );
    },
    []
  );

  // SSE stream lifecycle
  useEffect(() => {
    if (!enabled || !threadId) return;

    let isMounted = true;

    function connect() {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }

      const sinceSeq = lastEventIdRef.current;
      const url = `${apiBase}/threads/${threadId}/stream?since_seq=${sinceSeq}&follow=true`;

      try {
        const es = new EventSource(url);
        eventSourceRef.current = es;

        es.onopen = () => {
          if (!isMounted) return;
          setIsReconnecting(false);
        };

        es.addEventListener("token", (ev: MessageEvent) => {
          if (!isMounted) return;
          try {
            const data = JSON.parse(ev.data);
            const { message_id, delta } = data;
            if (!message_id) return;

            setActiveStreamingId(message_id);
            pendingTokenDeltasRef.current.set(
              message_id,
              (pendingTokenDeltasRef.current.get(message_id) || "") + (delta || ""),
            );
            scheduleTokenFlush();
          } catch {
            // ignore JSON parse error
          }
        });

        es.addEventListener("message", (ev: MessageEvent) => {
          if (!isMounted) return;
          flushTokenDeltas();
          if (ev.lastEventId) {
            const parsed = parseInt(ev.lastEventId, 10);
            if (!isNaN(parsed)) {
              lastEventIdRef.current = Math.max(lastEventIdRef.current, parsed);
            }
          }

          try {
            const frame: unknown = JSON.parse(ev.data);
            const isLivePublish =
              frame !== null &&
              typeof frame === "object" &&
              "message" in frame &&
              Boolean((frame as { message?: unknown }).message) &&
              typeof (frame as { message?: unknown }).message === "object";
            const incoming = messageFromFrame(frame);
            if (!incoming) return;
            if (isLivePublish) dispatchDashboardNavigation(incoming);
            if (incoming.seq && incoming.seq > lastEventIdRef.current) {
              lastEventIdRef.current = incoming.seq;
            }

            setMessages((prev) => {
              const existingIdx = prev.findIndex((m) => m.id === incoming.id);
              if (existingIdx !== -1) {
                const next = [...prev];
                next[existingIdx] = {
                  ...next[existingIdx],
                  ...incoming,
                  // If incoming message delivery is complete, stop streaming
                  streaming: incoming.delivery === "pending" ? next[existingIdx].streaming : false,
                };
                return next;
              }
              return [...prev, incoming];
            });

            if (incoming.delivery === "complete" || incoming.delivery === "failed") {
              setActiveStreamingId(null);
            }
          } catch {
            // ignore
          }
        });

        es.onerror = () => {
          if (!isMounted) return;
          flushTokenDeltas();
          setIsReconnecting(true);
          es.close();

          // Exponential backoff reconnect
          reconnectTimeoutRef.current = setTimeout(() => {
            if (isMounted) {
              connect();
            }
          }, 3000);
        };
      } catch {
        setIsReconnecting(true);
      }
    }

    connect();

    return () => {
      isMounted = false;
      if (tokenFrameRef.current !== null) {
        cancelAnimationFrame(tokenFrameRef.current);
        tokenFrameRef.current = null;
      }
      if (tokenTimeoutRef.current !== null) {
        clearTimeout(tokenTimeoutRef.current);
        tokenTimeoutRef.current = null;
      }
      pendingTokenDeltasRef.current.clear();
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [threadId, apiBase, enabled, flushTokenDeltas, scheduleTokenFlush]);

  return {
    messages,
    isReconnecting,
    activeStreamingId,
    stopStreaming,
    appendOptimisticMessage,
    updateMessage,
  };
}
