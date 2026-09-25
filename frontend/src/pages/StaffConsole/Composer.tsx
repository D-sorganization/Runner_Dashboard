import React, { useState, useEffect, useRef, useCallback } from "react";
import type { ComposerProps, MentionSuggestion, SlashCommand } from "./threadTypes";
import { SLASH_COMMANDS } from "./threadTypes";

/**
 * Generate a unique idempotency key for reliable message transmission.
 */
function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `idem_${crypto.randomUUID()}`;
  }
  return `idem_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

export const Composer: React.FC<ComposerProps> = ({
  threadId,
  onSend,
  disabled = false,
  isSending = false,
  placeholder = "Message staff (Enter to send, Shift+Enter for newline)...",
  availableRoles = [],
  className = "",
}) => {
  const draftKey = `staff_console_draft_${threadId}`;
  const [text, setText] = useState<string>(() => {
    try {
      return localStorage.getItem(draftKey) || "";
    } catch {
      return "";
    }
  });

  const [activeMentionIndex, setActiveMentionIndex] = useState<number>(-1);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [showSlashCommands, setShowSlashCommands] = useState<boolean>(false);
  const [slashQuery, setSlashQuery] = useState<string>("");
  const [activeSlashIndex, setActiveSlashIndex] = useState<number>(-1);
  const [sendError, setSendError] = useState<string | null>(null);
  const [isLocalSending, setIsLocalSending] = useState<boolean>(false);
  const [pendingIdempotencyKey, setPendingIdempotencyKey] = useState<string | null>(null);
  const [isListening, setIsListening] = useState<boolean>(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync draft to localStorage
  useEffect(() => {
    try {
      if (text.length > 0) {
        localStorage.setItem(draftKey, text);
      } else {
        localStorage.removeItem(draftKey);
      }
    } catch {
      // LocalStorage quota or access error ignored
    }
  }, [text, draftKey]);

  // Auto-resize textarea
  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      const maxHeight = 200;
      el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
    }
  }, []);

  useEffect(() => {
    adjustHeight();
  }, [text, adjustHeight]);

  // Mention parsing
  const updateQueryState = (val: string, cursor: number) => {
    const textBeforeCursor = val.slice(0, cursor);

    // Slash command check: message starts with '/'
    if (textBeforeCursor.startsWith("/")) {
      const parts = textBeforeCursor.split(/\s/);
      if (parts.length === 1) {
        setShowSlashCommands(true);
        setSlashQuery(parts[0].toLowerCase());
        setMentionQuery(null);
        return;
      }
    }
    setShowSlashCommands(false);

    // @mention check
    const match = textBeforeCursor.match(/@([a-zA-Z0-9_-]*)$/);
    if (match) {
      setMentionQuery(match[1].toLowerCase());
    } else {
      setMentionQuery(null);
    }
  };

  const filteredRoles = mentionQuery !== null
    ? availableRoles.filter(
        (r) =>
          r.name.toLowerCase().includes(mentionQuery) ||
          r.title.toLowerCase().includes(mentionQuery)
      )
    : [];

  const filteredSlashCommands = showSlashCommands
    ? SLASH_COMMANDS.filter((cmd) => cmd.name.toLowerCase().includes(slashQuery))
    : [];

  const handleSelectMention = (role: MentionSuggestion) => {
    const el = textareaRef.current;
    const cursor = el ? el.selectionStart : text.length;
    const textBeforeCursor = text.slice(0, cursor);
    const textAfterCursor = text.slice(cursor);
    const newBefore = textBeforeCursor.replace(/@([a-zA-Z0-9_-]*)$/, `@${role.name} `);
    const nextVal = newBefore + textAfterCursor;

    setText(nextVal);
    setMentionQuery(null);
    setActiveMentionIndex(-1);

    setTimeout(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(newBefore.length, newBefore.length);
      }
    }, 0);
  };

  const handleSelectSlash = (cmd: SlashCommand) => {
    const replacement = cmd.template || `${cmd.name} `;
    setText(replacement);
    setShowSlashCommands(false);
    setActiveSlashIndex(-1);

    setTimeout(() => {
      if (textareaRef.current) {
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(replacement.length, replacement.length);
      }
    }, 0);
  };

  const doSend = async (bodyToSend: string, key?: string) => {
    const trimmed = bodyToSend.trim();
    if (!trimmed) return;

    const idempotencyKey = key || generateIdempotencyKey();
    setPendingIdempotencyKey(idempotencyKey);
    setIsLocalSending(true);
    setSendError(null);

    try {
      await onSend(trimmed, idempotencyKey);
      setText("");
      try {
        localStorage.removeItem(draftKey);
      } catch {
        // Ignored
      }
      setPendingIdempotencyKey(null);
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : "Failed to send message";
      setSendError(errMsg);
    } finally {
      setIsLocalSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // If mention suggestions visible
    if (mentionQuery !== null && filteredRoles.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveMentionIndex((prev) => (prev + 1) % filteredRoles.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveMentionIndex((prev) => (prev <= 0 ? filteredRoles.length - 1 : prev - 1));
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && activeMentionIndex >= 0) {
        e.preventDefault();
        handleSelectMention(filteredRoles[activeMentionIndex]);
        return;
      }
      if (e.key === "Escape") {
        setMentionQuery(null);
        return;
      }
    }

    // If slash commands visible
    if (showSlashCommands && filteredSlashCommands.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveSlashIndex((prev) => (prev + 1) % filteredSlashCommands.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveSlashIndex((prev) => (prev <= 0 ? filteredSlashCommands.length - 1 : prev - 1));
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && activeSlashIndex >= 0) {
        e.preventDefault();
        handleSelectSlash(filteredSlashCommands[activeSlashIndex]);
        return;
      }
      if (e.key === "Escape") {
        setShowSlashCommands(false);
        return;
      }
    }

    // Plain Enter to submit, Shift+Enter for newline
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void doSend(text);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    updateQueryState(val, e.target.selectionStart);
  };

  const handleToggleVoice = () => {
    setIsListening((prev) => !prev);
  };

  const sendingNow = isSending || isLocalSending;

  return (
    <div className={`staff-composer-container ${className}`.trim()} style={{ position: "relative" }}>
      {/* Mention popup */}
      {mentionQuery !== null && filteredRoles.length > 0 && (
        <div
          role="listbox"
          aria-label="Role suggestions"
          className="staff-composer-mentions-menu"
          style={{
            position: "absolute",
            bottom: "100%",
            left: 0,
            zIndex: 100,
            background: "var(--color-bg-surface, #ffffff)",
            border: "1px solid var(--color-border, #e2e8f0)",
            borderRadius: "6px",
            boxShadow: "0 4px 12px rgba(0, 0, 0, 0.15)",
            maxHeight: "220px",
            overflowY: "auto",
            minWidth: "260px",
            marginBottom: "6px",
          }}
        >
          {filteredRoles.map((role, idx) => (
            <div
              key={role.name}
              role="option"
              aria-selected={idx === activeMentionIndex}
              onClick={() => handleSelectMention(role)}
              className={`staff-composer-mention-option ${
                idx === activeMentionIndex ? "staff-composer-mention-option--active" : ""
              }`}
              style={{
                padding: "8px 12px",
                cursor: "pointer",
                display: "flex",
                flexDirection: "column",
                backgroundColor: idx === activeMentionIndex ? "var(--color-bg-hover, #f1f5f9)" : "transparent",
              }}
            >
              <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>{role.title}</div>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary, #64748b)" }}>
                @{role.name}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Slash command popup */}
      {showSlashCommands && filteredSlashCommands.length > 0 && (
        <div
          role="listbox"
          aria-label="Slash commands"
          className="staff-composer-slash-menu"
          style={{
            position: "absolute",
            bottom: "100%",
            left: 0,
            zIndex: 100,
            background: "var(--color-bg-surface, #ffffff)",
            border: "1px solid var(--color-border, #e2e8f0)",
            borderRadius: "6px",
            boxShadow: "0 4px 12px rgba(0, 0, 0, 0.15)",
            maxHeight: "220px",
            overflowY: "auto",
            minWidth: "280px",
            marginBottom: "6px",
          }}
        >
          {filteredSlashCommands.map((cmd, idx) => (
            <div
              key={cmd.name}
              role="option"
              aria-selected={idx === activeSlashIndex}
              onClick={() => handleSelectSlash(cmd)}
              className={`staff-composer-slash-option ${
                idx === activeSlashIndex ? "staff-composer-slash-option--active" : ""
              }`}
              style={{
                padding: "8px 12px",
                cursor: "pointer",
                backgroundColor: idx === activeSlashIndex ? "var(--color-bg-hover, #f1f5f9)" : "transparent",
              }}
            >
              <div style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--color-primary, #2563eb)" }}>
                {cmd.name}
              </div>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary, #64748b)" }}>
                {cmd.description}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Error & Retry banner */}
      {sendError && (
        <div
          role="alert"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "var(--color-bg-danger-subtle, #fef2f2)",
            color: "var(--color-text-danger, #b91c1c)",
            border: "1px solid var(--color-border-danger, #fecaca)",
            borderRadius: "6px",
            padding: "6px 12px",
            marginBottom: "6px",
            fontSize: "0.8125rem",
          }}
        >
          <span>Failed to send message: {sendError}</span>
          <button
            type="button"
            aria-label="Retry send"
            onClick={() => void doSend(text, pendingIdempotencyKey || undefined)}
            style={{
              background: "var(--color-bg-danger, #dc2626)",
              color: "#ffffff",
              border: "none",
              borderRadius: "4px",
              padding: "3px 8px",
              cursor: "pointer",
              fontWeight: 500,
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* Main input controls */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: "8px",
          background: "var(--color-bg-surface, #ffffff)",
          border: "1px solid var(--color-border, #cbd5e1)",
          borderRadius: "8px",
          padding: "6px 10px",
        }}
      >
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled || sendingNow}
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            border: "none",
            outline: "none",
            fontSize: "0.9375rem",
            lineHeight: "1.4",
            padding: "4px 2px",
            maxHeight: "200px",
            fontFamily: "inherit",
          }}
        />

        {/* Voice Input Button */}
        <button
          type="button"
          aria-label={isListening ? "Stop voice input" : "Voice input"}
          onClick={handleToggleVoice}
          disabled={disabled || sendingNow}
          style={{
            background: isListening ? "var(--color-danger, #ef4444)" : "transparent",
            color: isListening ? "#ffffff" : "var(--color-text-secondary, #64748b)",
            border: "none",
            borderRadius: "4px",
            padding: "6px",
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          title={isListening ? "Listening... click to stop" : "Voice input"}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="22" />
          </svg>
        </button>

        {/* Send Button */}
        <button
          type="button"
          aria-label="Send message"
          onClick={() => void doSend(text)}
          disabled={disabled || sendingNow || text.trim().length === 0}
          style={{
            background: text.trim().length > 0 && !sendingNow ? "var(--color-primary, #2563eb)" : "var(--color-bg-disabled, #94a3b8)",
            color: "#ffffff",
            border: "none",
            borderRadius: "6px",
            padding: "6px 12px",
            cursor: text.trim().length > 0 && !sendingNow ? "pointer" : "not-allowed",
            fontWeight: 600,
            fontSize: "0.875rem",
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
          }}
        >
          {sendingNow ? "Sending..." : "Send"}
        </button>
      </div>
    </div>
  );
};
