/**
 * Thread.tsx — Staff Console conversation thread view with sanitized markdown,
 * streaming token deltas, stop button, date separators, jump to unread, and composer.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import type { ThreadMessage, ThreadProps } from "./threadTypes";
import { MessageItem } from "./MessageItem";
import { Composer } from "./Composer";

export function formatSeparatorDate(isoString?: string): string {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return "";
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);

    if (d.toDateString() === today.toDateString()) {
      return "Today";
    }
    if (d.toDateString() === yesterday.toDateString()) {
      return "Yesterday";
    }
    return d.toLocaleDateString([], {
      month: "short",
      day: "numeric",
      year: d.getFullYear() !== today.getFullYear() ? "numeric" : undefined,
    });
  } catch {
    return "";
  }
}

export function getDateKey(isoString?: string): string {
  if (!isoString) return "unknown";
  try {
    const d = new Date(isoString);
    return isNaN(d.getTime()) ? "unknown" : d.toISOString().slice(0, 10);
  } catch {
    return "unknown";
  }
}

export const DateSeparator: React.FC<{ label: string }> = ({ label }) => {
  return (
    <div
      role="separator"
      className="thread-date-separator"
      style={{
        display: "flex",
        alignItems: "center",
        margin: "16px 0 8px 0",
        color: "var(--text-muted, #8b949e)",
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
      }}
    >
      <div style={{ flex: 1, height: 1, background: "var(--border, #30363d)" }} />
      <span style={{ padding: "0 12px" }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: "var(--border, #30363d)" }} />
    </div>
  );
};

export const Thread: React.FC<ThreadProps> = ({
  thread,
  messages = [],
  unreadSeqThreshold,
  isReconnecting = false,
  onStopStreaming,
  onRetryMessage,
  onSendMessage,
  roles = [],
  className = "",
}) => {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const unreadTargetRef = useRef<HTMLDivElement>(null);
  const [showJumpToUnread, setShowJumpToUnread] = useState<boolean>(false);
  const [userScrolledUp, setUserScrolledUp] = useState<boolean>(false);

  // Group messages by date
  const groupedItems = useMemo(() => {
    const elements: Array<{ type: "separator" | "message"; key: string; label?: string; message?: ThreadMessage }> = [];
    let lastDateKey = "";

    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      const dateKey = getDateKey(msg.created_at);

      if (dateKey !== lastDateKey && dateKey !== "unknown") {
        elements.push({
          type: "separator",
          key: `sep-${dateKey}-${i}`,
          label: formatSeparatorDate(msg.created_at),
        });
        lastDateKey = dateKey;
      }

      elements.push({
        type: "message",
        key: `msg-${msg.id}`,
        message: msg,
      });
    }

    return elements;
  }, [messages]);

  // Check if unread messages exist
  const firstUnread = useMemo(() => {
    if (unreadSeqThreshold === undefined) return null;
    return messages.find((m) => (m.seq ?? 0) >= unreadSeqThreshold) || null;
  }, [messages, unreadSeqThreshold]);

  useEffect(() => {
    if (firstUnread) {
      setShowJumpToUnread(true);
    } else {
      setShowJumpToUnread(false);
    }
  }, [firstUnread]);

  // Auto-scroll to bottom on new messages unless user manually scrolled up
  useEffect(() => {
    if (!userScrolledUp && scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  }, [messages, userScrolledUp]);

  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    setUserScrolledUp(!isAtBottom);

    if (isAtBottom && showJumpToUnread) {
      setShowJumpToUnread(false);
    }
  };

  const handleJumpToUnread = () => {
    setShowJumpToUnread(false);
    if (unreadTargetRef.current && typeof unreadTargetRef.current.scrollIntoView === "function") {
      unreadTargetRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  };

  return (
    <div
      className={`staff-thread-view ${className}`}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        background: "var(--bg-primary, #0d1117)",
        color: "var(--text-primary, #c9d1d9)",
        position: "relative",
      }}
    >
      {/* Sticky Reconnecting Banner */}
      {isReconnecting && (
        <div
          role="status"
          className="thread-reconnecting-banner"
          style={{
            position: "sticky",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 10,
            background: "rgba(210, 153, 34, 0.2)",
            borderBottom: "1px solid rgba(210, 153, 34, 0.5)",
            color: "var(--accent-yellow, #e3b341)",
            padding: "6px 16px",
            fontSize: 12,
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
          }}
        >
          <span className="spinner" aria-hidden="true">↻</span>
          <span>Reconnecting to thread events…</span>
        </div>
      )}

      {/* Messages Scroll Area */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="thread-messages-scroll"
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "16px 20px",
          display: "flex",
          flexDirection: "column",
          gap: 4,
          minHeight: 0,
        }}
      >
        {groupedItems.map((item) => {
          if (item.type === "separator") {
            return <DateSeparator key={item.key} label={item.label || ""} />;
          }

          if (item.message) {
            const isUnreadTarget = firstUnread && item.message.id === firstUnread.id;
            return (
              <div
                key={item.key}
                ref={isUnreadTarget ? unreadTargetRef : undefined}
                className={isUnreadTarget ? "thread-unread-target" : undefined}
              >
                <MessageItem
                  message={item.message}
                  isStreaming={item.message.streaming}
                  onStopStreaming={onStopStreaming}
                  onRetry={onRetryMessage}
                />
              </div>
            );
          }

          return null;
        })}
      </div>

      {/* Floating Jump to Unread Button */}
      {showJumpToUnread && (
        <button
          type="button"
          aria-label="Jump to unread"
          onClick={handleJumpToUnread}
          style={{
            position: "absolute",
            bottom: 84,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--accent-blue, #1f6feb)",
            color: "#fff",
            border: "none",
            borderRadius: 20,
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            padding: "6px 16px",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            zIndex: 20,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span>Jump to unread</span>
          <span>↓</span>
        </button>
      )}

      {/* Composer Section */}
      {onSendMessage && (
        <Composer
          threadId={thread.id}
          roles={roles}
          onSendMessage={onSendMessage}
        />
      )}
    </div>
  );
};
