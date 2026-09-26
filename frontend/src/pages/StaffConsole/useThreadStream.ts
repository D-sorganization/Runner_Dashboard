/**
 * useThreadStream.ts — SSE hook for live Staff Console conversation events:
 * token deltas, message completions, reconnection with Last-Event-ID, and manual stream abort.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ThreadMessage } from "./threadTypes";

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
  initialMessages = [],
  apiBase = "/api/v1/staff",
  enabled = true,
}: UseThreadStreamOptions): UseThreadStreamResult {
  const [messages, setMessages] = useState<ThreadMessage[]>(initialMessages);
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [activeStreamingId, setActiveStreamingId] = useState<string | null>(null);

  const lastEventIdRef = useRef<number>(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Synchronize initial messages when thread changes
  useEffect(() => {
    setMessages(initialMessages);
    let maxSeq = 0;
    for (const m of initialMessages) {
      if (m.seq && m.seq > maxSeq) {
        maxSeq = m.seq;
      }
    }
    lastEventIdRef.current = maxSeq;
  }, [threadId, initialMessages]);

  const stopStreaming = useCallback((messageId?: string) => {
    setMessages((prev) =>
      prev.map((m) => {
        if (!messageId || m.id === messageId) {
          return { ...m, streaming: false, delivery: "complete" };
        }
        return m;
      })
    );
    setActiveStreamingId(null);
  }, []);

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
            setMessages((prev) => {
              const target = prev.find((m) => m.id === message_id);
              if (!target) {
                // If message placeholder hasn't arrived, create one
                return [
                  ...prev,
                  {
                    id: message_id,
                    thread_id: threadId,
                    author: "staff",
                    author_kind: "staff",
                    kind: "text",
                    body_md: delta || "",
                    delivery: "pending",
                    streaming: true,
                  },
                ];
              }
              return prev.map((m) =>
                m.id === message_id
                  ? {
                      ...m,
                      body_md: (m.body_md || "") + (delta || ""),
                      streaming: true,
                    }
                  : m
              );
            });
          } catch {
            // ignore JSON parse error
          }
        });

        es.addEventListener("message", (ev: MessageEvent) => {
          if (!isMounted) return;
          if (ev.lastEventId) {
            const parsed = parseInt(ev.lastEventId, 10);
            if (!isNaN(parsed)) {
              lastEventIdRef.current = parsed;
            }
          }

          try {
            const incoming = messageFromFrame(JSON.parse(ev.data));
            if (!incoming) return;
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
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [threadId, apiBase, enabled]);

  return {
    messages,
    isReconnecting,
    activeStreamingId,
    stopStreaming,
    appendOptimisticMessage,
    updateMessage,
  };
}
