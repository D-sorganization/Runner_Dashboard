import React from "react";
import type { MaxwellStatus, MaxwellTask } from "./MaxwellPage";

export interface ChatMessage {
  id: number;
  role: string;
  content: string;
  streaming?: boolean;
  error?: boolean;
  detail?: string;
}

interface MaxwellChatPanelProps {
  status: MaxwellStatus;
  chatMessages: ChatMessage[];
  chatInput: string;
  chatSending: boolean;
  showScrollButton: boolean;
  chatListRef: React.RefObject<HTMLDivElement | null>;
  onChatScroll: () => void;
  onChatInputChange: (value: string) => void;
  onChatKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSendChat: (text?: string) => void;
  onShowLatest: () => void;
  onRetry: () => void;
}

export function MaxwellChatPanel({
  status,
  chatMessages,
  chatInput,
  chatSending,
  showScrollButton,
  chatListRef,
  onChatScroll,
  onChatInputChange,
  onChatKeyDown,
  onSendChat,
  onShowLatest,
  onRetry,
}: MaxwellChatPanelProps): React.ReactElement {
  return (
    <div className="section maxwell-chat-section">
      <div className="section-header">
        <span className="section-title">Maxwell Chat</span>
      </div>
      <div className="section-body maxwell-chat">
        <div
          className="maxwell-chat-messages"
          ref={chatListRef as React.Ref<HTMLDivElement>}
          onScroll={onChatScroll}
          aria-live="polite"
        >
          {chatMessages.length === 0 ? (
            <div className="maxwell-chat-empty">
              {status.http_reachable
                ? "Ask Maxwell for fleet status, recent runner activity, or the next operator command."
                : "Maxwell-Daemon is unreachable. Chat history is preserved; use Retry after the daemon is reachable."}
            </div>
          ) : (
            chatMessages.map((msg) => (
              <div
                key={msg.id}
                className={
                  "maxwell-chat-bubble " +
                  msg.role +
                  (msg.error ? " error" : "")
                }
              >
                {msg.content || (msg.streaming ? "Streaming..." : "")}
                {msg.streaming ? (
                  <span style={{ color: "var(--text-muted)" }}> ▌</span>
                ) : null}
              </div>
            ))
          )}
        </div>
        {showScrollButton ? (
          <button
            aria-label="Scroll to bottom of chat"
            className="btn maxwell-scroll-button"
            onClick={onShowLatest}
          >
            Latest
          </button>
        ) : null}
        <div
          className="maxwell-quick-actions"
          aria-label="Maxwell quick actions"
        >
          {["status", "summarize last hour", "which runners are blocked?"].map(
            (chip) => (
              <button
                key={chip}
                className="btn"
                type="button"
                onClick={() => {
                  onSendChat(chip);
                }}
                disabled={chatSending}
              >
                {chip}
              </button>
            ),
          )}
          {!status.http_reachable ? (
            <button className="btn btn-blue" type="button" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
        <div className="maxwell-composer">
          <textarea
            value={chatInput}
            onChange={(e) => {
              onChatInputChange(e.target.value);
            }}
            onKeyDown={onChatKeyDown}
            placeholder={
              status.http_reachable
                ? "Message Maxwell..."
                : "Daemon unreachable; retry before sending commands"
            }
            rows={1}
            disabled={chatSending || !status.http_reachable}
            style={{
              width: "100%",
              boxSizing: "border-box",
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--bg-tertiary)",
              color: "var(--text-primary)",
              padding: "10px 12px",
              fontFamily: "inherit",
              fontSize: 13,
            }}
          />
          <button
            className="btn btn-blue"
            type="button"
            onClick={() => {
              onSendChat();
            }}
            disabled={
              chatSending || !chatInput.trim() || !status.http_reachable
            }
          >
            {chatSending ? "Sending..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface MaxwellTasksPanelProps {
  status: MaxwellStatus;
  tasks: MaxwellTask[];
  tasksLoading: boolean;
}

export function MaxwellTasksPanel({
  status,
  tasks,
  tasksLoading,
}: MaxwellTasksPanelProps): React.ReactElement {
  return (
    <div className="section">
      <div className="section-header">
        <span className="section-title">Recent Tasks</span>
      </div>
      <div className="section-body">
        {tasksLoading ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
            Loading tasks…
          </div>
        ) : !status.http_reachable ? (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Maxwell-Daemon offline — no task history
          </div>
        ) : tasks.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No tasks yet
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Status</th>
                <th>Repo</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td>{(task.id || "").slice(0, 8)}</td>
                  <td>{task.status || "—"}</td>
                  <td>{task.repo || "—"}</td>
                  <td>
                    {task.created_at
                      ? task.created_at.slice(0, 16).replace("T", " ")
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
