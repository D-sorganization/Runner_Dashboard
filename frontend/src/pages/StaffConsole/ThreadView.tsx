import React, { useEffect, useRef, useState, useMemo } from "react";
import type { ThreadViewProps } from "./threadTypes";
import { MessageItem } from "./MessageItem";
import { Composer } from "./Composer";

/**
 * Format timestamp into human-readable date for separators.
 */
function formatDateLabel(isoString: string): string {
  try {
    const d = new Date(isoString);
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);

    if (d.toDateString() === today.toDateString()) {
      return "Today";
    }
    if (d.toDateString() === yesterday.toDateString()) {
      return "Yesterday";
    }
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: d.getFullYear() !== today.getFullYear() ? "numeric" : undefined,
    });
  } catch {
    return isoString;
  }
}

function getDayKey(isoString: string): string {
  try {
    return new Date(isoString).toDateString();
  } catch {
    return isoString;
  }
}

export const ThreadView: React.FC<ThreadViewProps> = ({
  threadId,
  roleName,
  roleTitle,
  messages,
  isLoading = false,
  isStreaming = false,
  isReconnecting = false,
  onSendMessage,
  onRetryMessage,
  onStopStreaming,
  availableRoles = [],
  className = "",
}) => {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [userHasScrolledUp, setUserHasScrolledUp] = useState<boolean>(false);
  const lastMessageCountRef = useRef<number>(messages.length);

  // Group messages with date separators
  const renderedElements = useMemo(() => {
    const elements: React.ReactNode[] = [];
    let lastDay = "";

    messages.forEach((msg, idx) => {
      const day = getDayKey(msg.created_at);
      if (day !== lastDay) {
        elements.push(
          <div
            key={`sep-${day}-${idx}`}
            role="separator"
            className="staff-date-separator"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "16px 0 12px 0",
              position: "relative",
            }}
          >
            <div
              style={{
                position: "absolute",
                top: "50%",
                left: 0,
                right: 0,
                borderTop: "1px solid var(--color-border, #e2e8f0)",
                zIndex: 1,
              }}
            />
            <span
              style={{
                position: "relative",
                zIndex: 2,
                backgroundColor: "var(--color-bg-canvas, #f8fafc)",
                padding: "2px 10px",
                fontSize: "0.75rem",
                color: "var(--color-text-secondary, #64748b)",
                borderRadius: "10px",
                border: "1px solid var(--color-border, #e2e8f0)",
              }}
            >
              {formatDateLabel(msg.created_at)}
            </span>
          </div>
        );
        lastDay = day;
      }

      elements.push(
        <MessageItem
          key={msg.id}
          message={msg}
          onRetry={onRetryMessage}
        />
      );
    });

    return elements;
  }, [messages, onRetryMessage]);

  // Handle scroll detection
  const handleScroll = () => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    // If scrolled up more than 80px, note that user scrolled up
    setUserHasScrolledUp(distanceToBottom > 80);
  };

  // Auto-scroll on new messages unless scrolled up
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    if (!userHasScrolledUp || messages.length > lastMessageCountRef.current + 3) {
      container.scrollTop = container.scrollHeight;
    }
    lastMessageCountRef.current = messages.length;
  }, [messages.length, isStreaming, userHasScrolledUp]);

  const scrollToBottom = () => {
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
      setUserHasScrolledUp(false);
    }
  };

  return (
    <div
      className={`staff-thread-view ${className}`.trim()}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: "450px",
        background: "var(--color-bg-canvas, #f8fafc)",
        position: "relative",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--color-border, #e2e8f0)",
          background: "var(--color-bg-surface, #ffffff)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div>
          <div style={{ fontWeight: 600, fontSize: "1rem", color: "var(--color-text-primary, #0f172a)" }}>
            {roleTitle ? `${roleTitle} (@${roleName})` : `@${roleName}`}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary, #64748b)" }}>
            Thread: {threadId}
          </div>
        </div>

        {/* Reconnecting banner or status */}
        {isReconnecting && (
          <div
            role="status"
            style={{
              fontSize: "0.75rem",
              background: "var(--color-warning-subtle, #fef3c7)",
              color: "var(--color-warning-text, #92400e)",
              border: "1px solid var(--color-warning-border, #fde68a)",
              borderRadius: "4px",
              padding: "4px 8px",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                background: "#f59e0b",
                display: "inline-block",
                animation: "pulse 1.5s infinite",
              }}
            />
            Reconnecting to stream…
          </div>
        )}
      </div>

      {/* Messages Scroll Area */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "16px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {isLoading && messages.length === 0 && (
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              flex: 1,
              color: "var(--color-text-secondary, #64748b)",
              fontSize: "0.875rem",
            }}
          >
            Loading thread messages…
          </div>
        )}

        {renderedElements}

        {/* Streaming Indicator */}
        {isStreaming && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "8px 12px",
              background: "var(--color-bg-surface, #ffffff)",
              border: "1px dashed var(--color-primary-border, #93c5fd)",
              borderRadius: "8px",
              margin: "8px 0",
              fontSize: "0.8125rem",
              color: "var(--color-primary, #2563eb)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span
                style={{
                  display: "inline-block",
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  background: "#2563eb",
                  animation: "ping 1s cubic-bezier(0, 0, 0.2, 1) infinite",
                }}
              />
              Generating response…
            </div>
            {onStopStreaming && (
              <button
                type="button"
                aria-label="Stop generating"
                onClick={onStopStreaming}
                style={{
                  border: "1px solid var(--color-border-danger, #fca5a5)",
                  background: "var(--color-bg-danger-subtle, #fef2f2)",
                  color: "var(--color-text-danger, #b91c1c)",
                  borderRadius: "4px",
                  padding: "3px 8px",
                  fontSize: "0.75rem",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                Stop generating
              </button>
            )}
          </div>
        )}
      </div>

      {/* Jump to unread / latest floating button */}
      {userHasScrolledUp && (
        <button
          type="button"
          onClick={scrollToBottom}
          aria-label="Jump to latest messages"
          style={{
            position: "absolute",
            bottom: "80px",
            right: "24px",
            zIndex: 10,
            background: "var(--color-bg-surface, #ffffff)",
            color: "var(--color-primary, #2563eb)",
            border: "1px solid var(--color-border, #cbd5e1)",
            borderRadius: "20px",
            boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
            padding: "6px 14px",
            fontSize: "0.8125rem",
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: "4px",
          }}
        >
          ↓ Jump to latest
        </button>
      )}

      {/* Bottom Composer */}
      <div
        style={{
          padding: "12px 16px",
          borderTop: "1px solid var(--color-border, #e2e8f0)",
          background: "var(--color-bg-surface, #ffffff)",
        }}
      >
        <Composer
          threadId={threadId}
          onSend={onSendMessage}
          availableRoles={availableRoles}
        />
      </div>
    </div>
  );
};
