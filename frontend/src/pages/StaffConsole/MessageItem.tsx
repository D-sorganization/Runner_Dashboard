import React from "react";
import type { ThreadMessage } from "./threadTypes";
import { renderSanitizedMarkdown } from "./markdownRenderer";

export interface MessageItemProps {
  message: ThreadMessage;
  onRetry?: (messageId: string, idempotencyKey: string) => void;
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, onRetry }) => {
  const isUser = message.author_kind === "user";
  const isSystem = message.author_kind === "system";
  const isFailed = message.delivery === "failed" || message.kind === "error";

  const idempotencyKey = (message.meta?.idempotency_key as string) || "";

  const timeFormatted = (() => {
    try {
      const d = new Date(message.created_at);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  })();

  const bubbleClass = isUser
    ? "staff-message-item--user"
    : isSystem
    ? "staff-message-item--system"
    : "staff-message-item--role";

  return (
    <div
      className={`staff-message-item ${bubbleClass}`}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : isSystem ? "center" : "flex-start",
        marginBottom: "12px",
        width: "100%",
      }}
    >
      {/* Header with author and timestamp */}
      {!isSystem && (
        <div
          style={{
            fontSize: "0.75rem",
            color: "var(--color-text-secondary, #64748b)",
            marginBottom: "4px",
            display: "flex",
            gap: "6px",
            alignItems: "center",
          }}
        >
          <span style={{ fontWeight: 600 }}>{message.author}</span>
          {timeFormatted && <span>{timeFormatted}</span>}
          {message.delivery === "pending" && (
            <span style={{ fontStyle: "italic", color: "var(--color-text-muted, #94a3b8)" }}>
              Sending...
            </span>
          )}
        </div>
      )}

      {/* Bubble card */}
      <div
        style={{
          maxWidth: isSystem ? "90%" : "75%",
          padding: "10px 14px",
          borderRadius: isUser ? "12px 12px 2px 12px" : isSystem ? "8px" : "12px 12px 12px 2px",
          backgroundColor: isFailed
            ? "var(--color-bg-danger-subtle, #fef2f2)"
            : isUser
            ? "var(--color-primary-subtle, #eff6ff)"
            : isSystem
            ? "var(--color-bg-muted, #f8fafc)"
            : "var(--color-bg-surface, #ffffff)",
          border: isFailed
            ? "1px solid var(--color-border-danger, #fecaca)"
            : isUser
            ? "1px solid var(--color-primary-border, #bfdbfe)"
            : "1px solid var(--color-border, #e2e8f0)",
          color: isFailed ? "var(--color-text-danger, #991b1b)" : "inherit",
          boxShadow: "0 1px 2px rgba(0, 0, 0, 0.05)",
          fontSize: "0.9375rem",
          wordBreak: "break-word",
        }}
      >
        {/* Markdown Rendered Content */}
        <div>{renderSanitizedMarkdown(message.body_md)}</div>

        {/* Failed Delivery / Error Retry Section */}
        {isFailed && (
          <div
            style={{
              marginTop: "8px",
              paddingTop: "6px",
              borderTop: "1px solid var(--color-border-danger, #fecaca)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "8px",
            }}
          >
            <span style={{ fontSize: "0.75rem", color: "var(--color-text-danger, #b91c1c)" }}>
              Delivery failed
            </span>
            {onRetry && (
              <button
                type="button"
                aria-label="Retry message"
                onClick={() => onRetry(message.id, idempotencyKey)}
                style={{
                  background: "var(--color-bg-danger, #dc2626)",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "4px",
                  padding: "3px 8px",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Retry
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
