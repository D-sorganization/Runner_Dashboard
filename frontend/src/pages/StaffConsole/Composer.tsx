/**
 * Composer.tsx — Staff Console message input, supporting markdown, keyboard-first
 * @mentions and slash commands, voice input, persistent drafts, and reliable send with deduplication.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVoiceInput } from "../../hooks/useVoiceInput";
import type { ComposerProps, SlashCommand } from "./threadTypes";
import type { StaffRoleItem } from "./types";
import { ComposerAutocompletes } from "./ComposerAutocompletes";
import {
  clearDraft,
  filterMentionRoles,
  filterSlashCommands,
  generateIdempotencyKey,
  getDraft,
  getMentionQuery,
  getSlashCommandQuery,
  saveDraft,
} from "./composerUtils";

export const Composer: React.FC<ComposerProps> = ({
  threadId,
  roles = [],
  selectedRole,
  onSendMessage,
  disabled = false,
  placeholder,
  className = "",
  focusOnThreadChange = false,
}) => {
  const [text, setText] = useState<string>(() => getDraft(threadId));
  const [sendState, setSendState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);

  const [activeMenu, setActiveMenu] = useState<"mention" | "slash" | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const activeMenuRef = useRef(activeMenu);
  activeMenuRef.current = activeMenu;

  // Restore draft when threadId changes
  useEffect(() => {
    const saved = getDraft(threadId);
    setText(saved);
    setSendState("idle");
    setErrorMessage(null);
    setIdempotencyKey(null);
    setActiveMenu(null);
  }, [threadId]);

  // SC-D9: after a thread switch, keyboard focus follows the conversation.
  // The first render is skipped so opening the page never steals focus.
  const focusedThreadRef = useRef(threadId);
  useEffect(() => {
    if (!focusOnThreadChange || focusedThreadRef.current === threadId) return;
    focusedThreadRef.current = threadId;
    textareaRef.current?.focus();
  }, [focusOnThreadChange, threadId]);

  // Voice Input integration
  const voice = useVoiceInput({
    onTranscript: (transcript) => {
      setText((prev) => {
        const next = prev ? `${prev} ${transcript}` : transcript;
        saveDraft(threadId, next);
        return next;
      });
      setVoiceError(null);
    },
    onError: (err) => setVoiceError(err),
  });

  const getCaretPos = useCallback(() => {
    if (!textareaRef.current) return text.length;
    const pos = textareaRef.current.selectionStart;
    // In test runners or before focus, selectionStart can be 0 while text has content
    if (pos === 0 && text.length > 0 && !textareaRef.current.matches(":focus")) {
      return text.length;
    }
    return pos ?? text.length;
  }, [text.length]);

  // Autocomplete Queries
  const mentionQuery = useMemo(() => {
    const caret = getCaretPos();
    return getMentionQuery(text, caret);
  }, [text, getCaretPos]);

  const slashQuery = useMemo(() => {
    return getSlashCommandQuery(text);
  }, [text]);

  const matchedRoles = useMemo(() => {
    if (!mentionQuery) return [];
    return filterMentionRoles(mentionQuery.query, roles);
  }, [mentionQuery, roles]);

  const matchedCommands = useMemo(() => {
    if (!slashQuery) return [];
    return filterSlashCommands(slashQuery.query);
  }, [slashQuery]);

  // Update active menu based on queries
  useEffect(() => {
    if (mentionQuery && matchedRoles.length > 0) {
      setActiveMenu("mention");
      setSelectedIndex(0);
    } else if (slashQuery && matchedCommands.length > 0) {
      setActiveMenu("slash");
      setSelectedIndex(0);
    } else {
      setActiveMenu(null);
    }
  }, [mentionQuery, slashQuery, matchedRoles.length, matchedCommands.length]);

  const handleSelectRole = (role: StaffRoleItem) => {
    const caret = getCaretPos();
    const query = getMentionQuery(text, caret);
    const startIndex = query ? query.startIndex : text.lastIndexOf("@");
    const before = startIndex >= 0 ? text.slice(0, startIndex) : text;
    const after = text.slice(caret > startIndex ? caret : startIndex + 1);
    const nextText = `${before}@${role.name} ${after}`;
    setText(nextText);
    saveDraft(threadId, nextText);
    setActiveMenu(null);

    // Set caret after the inserted mention synchronously
    if (textareaRef.current) {
      const newPos = before.length + role.name.length + 2;
      textareaRef.current.value = nextText;
      textareaRef.current.focus();
      try {
        textareaRef.current.setSelectionRange(newPos, newPos);
      } catch {
        // ignore
      }
    }
  };

  const handleSelectCommand = (cmd: SlashCommand) => {
    const nextText = `${cmd.label} `;
    setText(nextText);
    saveDraft(threadId, nextText);
    setActiveMenu(null);

    if (textareaRef.current) {
      textareaRef.current.value = nextText;
      textareaRef.current.focus();
      try {
        textareaRef.current.setSelectionRange(nextText.length, nextText.length);
      } catch {
        // ignore
      }
    }
  };

  const executeSend = async (retryKey?: string) => {
    const trimmed = text.trim();
    if (!trimmed || sendState === "sending" || disabled) return;

    const key = retryKey || idempotencyKey || generateIdempotencyKey(threadId);
    setIdempotencyKey(key);
    setSendState("sending");
    setErrorMessage(null);

    try {
      const result = await onSendMessage({
        body: trimmed,
        idempotencyKey: key,
        role: selectedRole,
      });
      // useStaffConsole reports failure as { ok: false } rather than throwing.
      if (result && result.ok === false) {
        throw new Error(typeof result.error === "string" ? result.error : "Failed to send message");
      }

      // Successful send: clear draft and reset input
      clearDraft(threadId);
      setText("");
      setIdempotencyKey(null);
      setSendState("sent");
      setTimeout(() => setSendState("idle"), 1000);
    } catch (err: unknown) {
      // Failed send: preserve draft & keep idempotency key for retry
      setSendState("failed");
      const msg = err instanceof Error ? err.message : "Failed to send message";
      setErrorMessage(msg);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (activeMenu === "mention") {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % matchedRoles.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + matchedRoles.length) % matchedRoles.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const chosen = matchedRoles[selectedIndex];
        if (chosen) handleSelectRole(chosen);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setActiveMenu(null);
        return;
      }
    }

    if (activeMenu === "slash") {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % matchedCommands.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + matchedCommands.length) % matchedCommands.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const chosen = matchedCommands[selectedIndex];
        if (chosen) handleSelectCommand(chosen);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setActiveMenu(null);
        return;
      }
    }

    // Normal Enter sends, Shift+Enter makes newline
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      executeSend();
    }
  };

  const defaultPlaceholder =
    placeholder || (selectedRole ? `Message ${selectedRole}…` : "Message Barb or type /dispatch…");

  return (
    <div
      className={`staff-composer ${className}`}
      style={{
        position: "relative",
        borderTop: "1px solid var(--border, #30363d)",
        background: "var(--bg-secondary, #0d1117)",
        padding: "12px 16px",
      }}
    >
      {/* Autocomplete Popup */}
      <ComposerAutocompletes
        activeMenu={activeMenu}
        matchedRoles={matchedRoles}
        matchedCommands={matchedCommands}
        selectedIndex={selectedIndex}
        onSelectRole={handleSelectRole}
        onSelectCommand={handleSelectCommand}
      />

      {/* Send Error & Retry Banner */}
      {errorMessage && (
        <div
          role="alert"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "rgba(248, 81, 73, 0.12)",
            border: "1px solid rgba(248, 81, 73, 0.4)",
            borderRadius: 6,
            padding: "6px 12px",
            marginBottom: 8,
            fontSize: 12,
            color: "var(--accent-red, #f85149)",
          }}
        >
          <span>Failed to send message: {errorMessage}</span>
          <button
            type="button"
            onClick={() => executeSend(idempotencyKey || undefined)}
            style={{
              background: "var(--accent-red, #f85149)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              padding: "2px 10px",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* Voice Error Banner */}
      {voiceError && (
        <div
          role="alert"
          style={{
            background: "rgba(248, 81, 73, 0.12)",
            border: "1px solid rgba(248, 81, 73, 0.4)",
            borderRadius: 6,
            padding: "4px 8px",
            marginBottom: 6,
            fontSize: 11,
            color: "var(--accent-red, #f85149)",
          }}
        >
          Voice error: {voiceError}
        </div>
      )}

      {/* Input Row */}
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea
          ref={textareaRef}
          value={text}
          disabled={disabled || sendState === "sending"}
          onChange={(e) => {
            setText(e.target.value);
            saveDraft(threadId, e.target.value);
          }}
          onKeyDown={handleKeyDown}
          placeholder={defaultPlaceholder}
          rows={2}
          aria-label="Staff conversation input"
          style={{
            flex: 1,
            background: "var(--bg-tertiary, #161b22)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: 8,
            color: "var(--text-primary, #c9d1d9)",
            fontFamily: "inherit",
            fontSize: 13,
            lineHeight: 1.45,
            padding: "8px 12px",
            resize: "vertical",
            minHeight: 52,
            boxSizing: "border-box",
            outline: "none",
          }}
        />

        {/* Voice Input Button */}
        <button
          type="button"
          aria-label={
            !voice.available
              ? "Voice input not supported"
              : voice.recording
                ? "Stop voice input"
                : "Voice input"
          }
          aria-pressed={voice.recording}
          onClick={voice.available ? voice.toggle : undefined}
          disabled={disabled || sendState === "sending" || !voice.available}
          title={!voice.available ? "Voice input is not supported in this browser" : undefined}
          style={{
            height: 40,
            width: 40,
            borderRadius: 8,
            border: "1px solid var(--border, #30363d)",
            background: voice.recording
              ? "var(--accent-red, #f85149)"
              : "var(--bg-tertiary, #161b22)",
            color: voice.recording ? "#fff" : "var(--text-secondary, #c9d1d9)",
            cursor: voice.available ? "pointer" : "not-allowed",
            opacity: voice.available ? 1 : 0.5,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 16,
            flexShrink: 0,
          }}
        >
          {voice.recording ? "■" : "🎙"}
        </button>

        {/* Send Button */}
        <button
          type="button"
          aria-label="Send message"
          onClick={() => executeSend()}
          disabled={disabled || sendState === "sending" || !text.trim()}
          style={{
            height: 40,
            padding: "0 16px",
            borderRadius: 8,
            border: "none",
            background: "var(--accent-blue, #1f6feb)",
            color: "#fff",
            fontWeight: 600,
            fontSize: 13,
            cursor: !text.trim() || sendState === "sending" ? "not-allowed" : "pointer",
            opacity: !text.trim() || sendState === "sending" ? 0.6 : 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {sendState === "sending" ? "…" : "Send ⮐"}
        </button>
      </div>
    </div>
  );
};
