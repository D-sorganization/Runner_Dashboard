/**
 * MessageItem.tsx — Renders individual thread messages: user bubbles,
 * staff markdown with code copying and link previews, streaming deltas with stop button,
 * and classified error cards with remediation (SC-D4, Issue #1318).
 */
import React from "react";
import type { ThreadMessage } from "./threadTypes";
import { ThreadMarkdown } from "./threadMarkdown";
import { formatFailureTitle, formatMessageTime } from "./threadUtils";

export interface MessageItemProps {
  message: ThreadMessage;
  isStreaming?: boolean;
  onStopStreaming?: (messageId: string) => void;
  onRetry?: (message: ThreadMessage) => void;
}

export const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isStreaming = false,
  onStopStreaming,
  onRetry,
}) => {
  const isUser = message.author_kind === "user" || message.author === "user";
  const isFailed = message.delivery === "failed" || message.kind === "error";
  const isActivelyStreaming = isStreaming || message.streaming;
  const timeStr = formatMessageTime(message.created_at);

  return (
    <div
      className={`thread-message-item ${isUser ? "thread-message-item--user" : "thread-message-item--agent"}`}
      data-testid={`message-${message.id}`}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
        margin: "8px 0",
        maxWidth: "100%",
      }}
    >
      {/* Header with Author and Time */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
          fontSize: 11,
          color: "var(--text-muted, #8b949e)",
        }}
      >
        <span style={{ fontWeight: 600, color: isUser ? "var(--text-secondary, #c9d1d9)" : "var(--accent-blue, #58a6ff)" }}>
          {isUser ? "You" : message.author.charAt(0).toUpperCase() + message.author.slice(1)}
        </span>
        {timeStr && <span>{timeStr}</span>}
        {message.delivery === "pending" && !isActivelyStreaming && (
          <span style={{ fontStyle: "italic" }}>sending…</span>
        )}
      </div>

      {/* Message Body or Error Card */}
      {isFailed ? (
        <div
          role="alert"
          className="thread-error-card"
          style={{
            background: "rgba(248, 81, 73, 0.1)",
            border: "1px solid rgba(248, 81, 73, 0.4)",
            borderRadius: 8,
            padding: "12px 16px",
            maxWidth: "85%",
            color: "var(--text-primary, #c9d1d9)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <span style={{ color: "var(--accent-red, #f85149)", fontWeight: 700, fontSize: 13 }}>
              ✖ {formatFailureTitle(message.failure_class)}
            </span>
          </div>
          {message.body_md && (
            <div style={{ fontSize: 12, marginBottom: 8, color: "var(--text-secondary, #c9d1d9)" }}>
              {message.body_md}
            </div>
          )}
          {message.remediation && (
            <div
              style={{
                fontSize: 12,
                background: "rgba(0, 0, 0, 0.2)",
                padding: "6px 10px",
                borderRadius: 4,
                marginBottom: 8,
                borderLeft: "3px solid var(--accent-red, #f85149)",
              }}
            >
              <strong>Remediation:</strong> {message.remediation}
            </div>
          )}
          {onRetry && (
            <button
              type="button"
              onClick={() => onRetry(message)}
              style={{
                background: "var(--accent-red, #f85149)",
                color: "#fff",
                border: "none",
                borderRadius: 4,
                padding: "4px 12px",
                fontSize: 12,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              ↻ Retry Turn
            </button>
          )}
        </div>
      ) : (
        <div
          className={`thread-bubble ${isUser ? "thread-bubble--user" : "thread-bubble--agent"}`}
          style={{
            background: isUser ? "var(--accent-blue, #1f6feb)" : "var(--bg-tertiary, #161b22)",
            color: isUser ? "#fff" : "var(--text-primary, #c9d1d9)",
            border: isUser ? "none" : "1px solid var(--border, #30363d)",
            borderRadius: isUser ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
            padding: "10px 14px",
            maxWidth: "85%",
            wordBreak: "break-word",
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          {isUser ? (
            <div style={{ whiteSpace: "pre-wrap" }}>{message.body_md}</div>
          ) : (
            <ThreadMarkdown content={message.body_md} />
          )}

          {/* Streaming Indicator & Stop Button */}
          {isActivelyStreaming && (
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                marginTop: 6,
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  color: isUser ? "rgba(255, 255, 255, 0.7)" : "var(--accent-blue, #58a6ff)",
                  animation: "pulse 1s infinite",
                  fontWeight: 900,
                }}
              >
                ▌
              </span>
              {onStopStreaming && (
                <button
                  type="button"
                  aria-label="Stop generating"
                  onClick={() => onStopStreaming(message.id)}
                  style={{
                    background: "rgba(248, 81, 73, 0.15)",
                    border: "1px solid rgba(248, 81, 73, 0.4)",
                    borderRadius: 4,
                    color: "var(--accent-red, #f85149)",
                    cursor: "pointer",
                    fontSize: 11,
                    fontWeight: 600,
                    padding: "2px 8px",
                  }}
                >
                  ■ Stop
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
