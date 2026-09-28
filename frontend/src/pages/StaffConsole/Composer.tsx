/**
 * Composer.tsx — Staff Console message input, supporting markdown, keyboard-first
 * @mentions and slash commands, voice input, persistent drafts, auto-growing input,
 * and reliable send with deduplication.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350) / Workstream B (#1720).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVoiceInput } from "../../hooks/useVoiceInput";
import type { ComposerProps as BaseComposerProps, SlashCommand } from "./threadTypes";
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
import "./composer.css";
import { MicGlyph } from "../decompIcons";

interface ComposerComponentProps extends BaseComposerProps {
  prefilledText?: string;
}

export const Composer: React.FC<ComposerComponentProps> = ({
  threadId,
  roles = [],
  selectedRole,
  onSendMessage,
  disabled = false,
  placeholder,
  className = "",
  focusOnThreadChange = false,
  isPanel = false,
  prefilledText,
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

  // Handle external prefill (e.g. empty-state suggestion chips)
  useEffect(() => {
    if (prefilledText) {
      setText(prefilledText);
      saveDraft(threadId, prefilledText);
      textareaRef.current?.focus();
    }
  }, [prefilledText, threadId]);

  // SC-D9: after a thread switch, keyboard focus follows the conversation.
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
    if (!trimmed || sendState === "sending" || disabled || isPanel) return;

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
      if (result && result.ok === false) {
        throw new Error(typeof result.error === "string" ? result.error : "Failed to send message");
      }

      clearDraft(threadId);
      setText("");
      setIdempotencyKey(null);
      setSendState("sent");
      setTimeout(() => setSendState("idle"), 1000);
    } catch (err: unknown) {
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

  const panelDisabledMessage = "Panels run on their own; start a new panel to ask again.";
  const roleItem = roles.find((r) => r.name === selectedRole);
  const roleDisplayName = roleItem?.title || (selectedRole ? selectedRole.charAt(0).toUpperCase() + selectedRole.slice(1) : "");
  const defaultPlaceholder = isPanel
    ? panelDisabledMessage
    : placeholder || (roleDisplayName ? `Message ${roleDisplayName}…` : "Message Barb or type /dispatch…");

  // Auto-grow rows from 1 to 10
  const lineCount = (text.match(/\n/g) || []).length + 1;
  const rows = Math.min(10, Math.max(1, lineCount));
  const isSendDisabled = disabled || isPanel || sendState === "sending" || !text.trim();

  return (
    <div className={`staff-composer ${className}`} role="region" aria-label="Message composer">
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
            background: "var(--badge-danger-bg)",
            border: "1px solid var(--accent-red)",
            borderRadius: 6,
            padding: "6px 12px",
            marginBottom: 8,
            fontSize: 12,
            color: "var(--accent-red)",
          }}
        >
          <span>Failed to send message: {errorMessage}</span>
          <button
            type="button"
            onClick={() => executeSend(idempotencyKey || undefined)}
            style={{
              background: "var(--accent-red)",
              color: "var(--text-on-accent)",
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
            background: "var(--badge-danger-bg)",
            border: "1px solid var(--accent-red)",
            borderRadius: 6,
            padding: "4px 8px",
            marginBottom: 6,
            fontSize: 11,
            color: "var(--accent-red)",
          }}
        >
          Voice error: {voiceError}
        </div>
      )}

      {/* Panel Read-Only Notice */}
      {isPanel && (
        <div
          role="status"
          className="composer-panel-notice"
          style={{
            background: "var(--badge-info-bg)",
            border: "1px solid var(--accent-blue)",
            borderRadius: 6,
            padding: "6px 12px",
            marginBottom: 8,
            fontSize: 12,
            color: "var(--text-secondary)",
          }}
        >
          {panelDisabledMessage}
        </div>
      )}

      {/* Claude-grade Rounded Bordered Box */}
      <div className="staff-composer__box">
        <textarea
          ref={textareaRef}
          value={text}
          disabled={disabled || isPanel || sendState === "sending"}
          onChange={(e) => {
            setText(e.target.value);
            saveDraft(threadId, e.target.value);
          }}
          onKeyDown={handleKeyDown}
          placeholder={defaultPlaceholder}
          rows={rows}
          aria-label="Staff conversation input"
          className="staff-composer__textarea"
          style={{
            overflowY: rows >= 10 ? "auto" : "hidden",
            maxHeight: "220px",
          }}
        />

        <div className="staff-composer__footer">
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
            disabled={disabled || isPanel || sendState === "sending" || !voice.available}
            title={!voice.available ? "Voice input is not supported in this browser" : undefined}
            className={`staff-composer__voice-btn ${voice.recording ? "staff-composer__voice-btn--recording" : ""}`}
            style={{
              cursor: voice.available && !isPanel ? "pointer" : "not-allowed",
              opacity: voice.available && !isPanel ? 1 : 0.4,
            }}
          >
            {voice.recording ? "■" : <MicGlyph size={16} />}
          </button>

          {/* Circular Accent Send Button */}
          <button
            type="button"
            aria-label="Send message"
            onClick={() => executeSend()}
            disabled={isSendDisabled}
            className="staff-composer__send-btn"
          >
            {sendState === "sending" ? (
              <span style={{ fontSize: 13, lineHeight: 1 }}>…</span>
            ) : (
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Muted Hint Line */}
      <div className="staff-composer__hint">
        Enter to send, Shift+Enter for a new line, @ to mention
      </div>
    </div>
  );
};
