import React, { useEffect, useRef, useState } from "react";
import { TouchButton } from "../../primitives/TouchButton";
import { useVoiceInput } from "../../hooks/useVoiceInput";
import { ChatBubble } from "./ChatBubble";
import type { ChatMessage, MaxwellStatus } from "./mobileTypes";
import { QUICK_CHIPS } from "./mobileTypes";

interface MaxwellChatProps {
  status: MaxwellStatus;
  chatMessages: ChatMessage[];
  chatInput: string;
  setChatInput: (v: string) => void;
  chatSending: boolean;
  sendChat: (text?: string) => void;
  onRetry: () => void;
}

export function MaxwellChat({
  status,
  chatMessages,
  chatInput,
  setChatInput,
  chatSending,
  sendChat,
  onRetry,
}: MaxwellChatProps) {
  const chatListRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  const voice = useVoiceInput({
    onTranscript: (text) => {
      setChatInput(chatInput ? `${chatInput} ${text}` : text);
      setVoiceError(null);
    },
    onError: (msg) => setVoiceError(msg),
  });

  // Auto-scroll chat when new messages arrive (unless user scrolled up)
  useEffect(() => {
    if (showScrollBtn) return;
    const el = chatListRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [chatMessages, showScrollBtn]);

  function isNearBottom(): boolean {
    const el = chatListRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 56;
  }

  function onChatScroll() {
    setShowScrollBtn(!isNearBottom());
  }

  function onChatKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  }

  return (
    <div
      className="maxwell-chat-section"
      style={{
        display: "flex",
        flexDirection: "column",
        flexGrow: 1,
        minHeight: 0,
      }}
    >
      <div
        style={{
          color: "var(--text-secondary)",
          fontSize: 12,
          fontWeight: 600,
          marginBottom: 8,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        Chat
      </div>

      {/* Message history */}
      <div
        ref={chatListRef}
        aria-label="Maxwell chat history"
        aria-live="polite"
        className="maxwell-chat-messages"
        onScroll={onChatScroll}
        style={{
          border: "1px solid var(--border)",
          borderRadius: 10,
          display: "flex",
          flexDirection: "column",
          flexGrow: 1,
          gap: 8,
          minHeight: 180,
          maxHeight: 280,
          overflowY: "auto",
          padding: "12px",
          WebkitOverflowScrolling:
            "touch" as React.CSSProperties["WebkitOverflowScrolling"],
        }}
      >
        {chatMessages.length === 0 ? (
          <div
            aria-label="No chat messages yet"
            style={{
              color: "var(--text-muted)",
              fontSize: 12,
              textAlign: "center",
              margin: "auto",
            }}
          >
            {status.http_reachable
              ? "Ask Maxwell for fleet status, runner activity, or the next operator command."
              : "Maxwell-Daemon is unreachable. Chat history is preserved; retry when daemon is reachable."}
          </div>
        ) : (
          chatMessages.map((m) => <ChatBubble key={m.id} message={m} />)
        )}
      </div>

      {/* Scroll-to-bottom button */}
      {showScrollBtn && (
        <TouchButton
          aria-label="Scroll to latest chat message"
          onClick={() => {
            setShowScrollBtn(false);
            if (chatListRef.current) {
              chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
            }
          }}
          variant="default"
          style={{
            alignSelf: "center",
            fontSize: 11,
            marginTop: 4,
            minHeight: 30,
            padding: "2px 10px",
          }}
        >
          Latest ↓
        </TouchButton>
      )}

      {/* Quick-action chips */}
      <div
        aria-label="Maxwell quick actions"
        style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}
      >
        {QUICK_CHIPS.map((chip) => (
          <TouchButton
            key={chip}
            aria-label={`Ask Maxwell: ${chip}`}
            disabled={chatSending}
            onClick={() => sendChat(chip)}
            variant="default"
            style={{ fontSize: 11, minHeight: 30, padding: "4px 10px" }}
          >
            {chip}
          </TouchButton>
        ))}
        {!status.http_reachable && (
          <TouchButton
            aria-label="Retry Maxwell connection"
            onClick={onRetry}
            variant="primary"
            style={{ fontSize: 11, minHeight: 30, padding: "4px 10px" }}
          >
            Retry
          </TouchButton>
        )}
      </div>

      {/* Composer */}
      {voiceError && (
        <div
          aria-live="polite"
          role="alert"
          style={{
            background: "rgba(248,81,73,0.12)",
            border: "1px solid rgba(248,81,73,0.35)",
            borderRadius: 6,
            color: "var(--accent-red)",
            fontSize: 11,
            marginTop: 6,
            padding: "4px 8px",
          }}
        >
          {voiceError}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <textarea
          aria-label="Message Maxwell"
          disabled={chatSending || !status.http_reachable}
          onChange={(e) => setChatInput(e.target.value)}
          onKeyDown={onChatKeyDown}
          placeholder={
            status.http_reachable
              ? "Message Maxwell…"
              : "Daemon unreachable; retry before sending commands"
          }
          rows={1}
          value={chatInput}
          style={{
            background: "var(--bg-tertiary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            boxSizing: "border-box",
            color: "var(--text-primary)",
            flex: 1,
            fontFamily: "inherit",
            fontSize: 13,
            minHeight: 40,
            padding: "10px 12px",
            resize: "none",
          }}
        />
        {voice.available && (
          <TouchButton
            aria-label={
              voice.recording ? "Stop voice recording" : "Start voice input"
            }
            aria-pressed={voice.recording}
            data-testid="maxwell-mic-btn"
            disabled={chatSending || !status.http_reachable}
            onClick={voice.toggle}
            variant={voice.recording ? "primary" : "default"}
            style={{
              flexShrink: 0,
              minHeight: 40,
              minWidth: 40,
              padding: "0 10px",
              outline: voice.recording
                ? "2px solid var(--accent-green)"
                : undefined,
            }}
          >
            {voice.recording ? "[REC]" : "🎙"}
          </TouchButton>
        )}
        <TouchButton
          aria-label="Send message to Maxwell"
          data-testid="maxwell-send-btn"
          disabled={chatSending || !chatInput.trim() || !status.http_reachable}
          onClick={() => sendChat()}
          variant="primary"
          style={{ flexShrink: 0, minHeight: 40, padding: "0 16px" }}
        >
          {chatSending ? "…" : "Send"}
        </TouchButton>
      </div>
    </div>
  );
}
