import type { ChatMessage } from "./mobileTypes";

interface ChatBubbleProps {
  message: ChatMessage;
}

export function ChatBubble({ message }: ChatBubbleProps) {
  const isOperator = message.role === "operator";
  return (
    <div
      className={`maxwell-chat-bubble ${message.role}${message.error ? " error" : ""}`}
      style={{
        alignSelf: isOperator ? "flex-end" : "flex-start",
        background: isOperator
          ? "var(--accent-blue)"
          : message.error
            ? "rgba(248,81,73,0.12)"
            : "var(--bg-secondary)",
        border: message.error ? "1px solid rgba(248,81,73,0.35)" : undefined,
        borderRadius: isOperator ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
        color: isOperator
          ? "#fff"
          : message.error
            ? "var(--accent-red)"
            : "var(--text-primary)",
        fontSize: 13,
        maxWidth: "84%",
        padding: "10px 14px",
        wordBreak: "break-word",
      }}
    >
      {message.content || (message.streaming ? "Receiving…" : "")}
      {message.streaming && (
        <span
          aria-hidden="true"
          style={{
            color: isOperator ? "rgba(255,255,255,0.6)" : "var(--text-muted)",
          }}
        >
          {" ▌"}
        </span>
      )}
    </div>
  );
}
