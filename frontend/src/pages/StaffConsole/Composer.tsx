/**
 * Composer.tsx — Staff Console message input, supporting markdown, keyboard-first
 * @mentions and slash commands, voice input, persistent drafts, auto-growing input,
 * and reliable send with deduplication.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350) / Workstream B (#1720).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVoiceInput } from "../../hooks/useVoiceInput";
import type { ComposerAttachment, ComposerProps as BaseComposerProps, IntakeSensitivity, SlashCommand } from "./threadTypes";
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
  uploadIntakeFile,
} from "./composerUtils";
import "./composer.css";
import { MicGlyph } from "../decompIcons";

function sensitivityLabel(sensitivity: IntakeSensitivity): string {
  switch (sensitivity) {
    case "public_reference": return "Library reference";
    case "confidential_work": return "Confidential work";
    case "vault_sensitive": return "Vault sensitive";
  }
}

function attachmentStatusLabel(status: ComposerAttachment["status"]): string {
  switch (status) {
    case "pending": return "Uploading…";
    case "attached": return "Ready";
    case "rejected": return "Needs attention";
  }
}

interface ComposerComponentProps extends BaseComposerProps {
  prefilledText?: string;
  attachmentDisabled?: boolean;
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
  autoFocus = false,
  isPanel = false,
  prefilledText,
  attachmentDisabled = false,
}) => {
  const [text, setText] = useState<string>(() => getDraft(threadId));
  const [sendState, setSendState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);

  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [sensitivity, setSensitivity] = useState<IntakeSensitivity>("confidential_work");
  const [activeMenu, setActiveMenu] = useState<"mention" | "slash" | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeMenuRef = useRef(activeMenu);
  activeMenuRef.current = activeMenu;
  const prevThreadIdRef = useRef(threadId);
  const latestTextRef = useRef(text);
  latestTextRef.current = text;

  useEffect(() => {
    if (!autoFocus || disabled || isPanel) return;
    const active = document.activeElement;
    if (!active || active === document.body || active === document.documentElement) {
      textareaRef.current?.focus();
    }
  }, [autoFocus, disabled, isPanel]);

  // Restore draft when threadId changes without wiping in-flight typing from "new"
  useEffect(() => {
    const saved = getDraft(threadId);
    const keepNew = prevThreadIdRef.current === "new" && threadId !== "new" && !saved && latestTextRef.current.trim().length > 0;
    const nextText = keepNew ? latestTextRef.current : saved;
    if (keepNew) { saveDraft(threadId, nextText); clearDraft("new"); }
    prevThreadIdRef.current = threadId;
    setText(nextText);
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
      textareaRef.current?.focus();
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

  const attachedItems = useMemo(() => attachments.filter((a) => a.status === "attached"), [attachments]);
  const hasUnsettledAttachments = attachments.some((item) => item.status !== "attached");
  const stageFiles = async (files: FileList | null) => {
    if (attachmentDisabled || !files?.length) return;
    const staged = Array.from(files).map((file) => {
      const id = "upload-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7);
      return {
        id,
        file,
        pending: {
          id,
          filename: file.name,
          sizeBytes: file.size,
          mimeType: file.type || "application/octet-stream",
          sensitivity,
          status: "pending" as const,
        },
      };
    });
    setAttachments((prev) => [...prev, ...staged.map((item) => item.pending)]);
    if (fileInputRef.current) fileInputRef.current.value = "";
    await Promise.all(staged.map(async ({ id, file }) => {
      const uploaded = await uploadIntakeFile(file, sensitivity, threadId);
      setAttachments((prev) => prev.map((item) => item.id === id ? { ...uploaded, id } : item));
    }));
    textareaRef.current?.focus();
  };

  const executeSend = async (retryKey?: string) => {
    const trimmed = text.trim();
    if ((!trimmed && !attachedItems.length) || hasUnsettledAttachments || sendState === "sending" || disabled || isPanel) return;

    const key = retryKey || idempotencyKey || generateIdempotencyKey(threadId);
    setIdempotencyKey(key);
    setSendState("sending");
    setErrorMessage(null);

    try {
      const result = await onSendMessage({
        body: trimmed || `Attached ${attachedItems.map((a) => a.filename).join(", ")}`,
        idempotencyKey: key,
        role: selectedRole,
        ...(attachedItems.length ? { meta: { attachments: attachedItems.map((a) => ({
          filename: a.filename, size_bytes: a.sizeBytes, mime_type: a.mimeType,
          sensitivity: a.sensitivity, sha256: a.sha256, intake_id: a.intakeId,
        })) } } : {}),
      });
      if (result && result.ok === false) {
        throw new Error(typeof result.error === "string" ? result.error : "Failed to send message");
      }

      clearDraft(threadId);
      setText("");
      setAttachments([]);
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

    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "v" && voice.available) {
      e.preventDefault();
      voice.toggle();
      return;
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
  const isSendDisabled = disabled || isPanel || sendState === "sending" || hasUnsettledAttachments || (!text.trim() && !attachedItems.length);

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

      {errorMessage && (
        <div role="alert" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--badge-danger-bg)", border: "1px solid var(--accent-red)", borderRadius: 6, padding: "6px 12px", marginBottom: 8, fontSize: 12, color: "var(--accent-red)" }}>
          <span>Failed to send message: {errorMessage}</span>
          <button type="button" onClick={() => executeSend(idempotencyKey || undefined)} disabled={hasUnsettledAttachments || sendState === "sending"} style={{ background: "var(--accent-red)", color: "var(--text-on-accent)", border: "none", borderRadius: 4, padding: "2px 10px", cursor: hasUnsettledAttachments || sendState === "sending" ? "not-allowed" : "pointer", opacity: hasUnsettledAttachments || sendState === "sending" ? 0.6 : 1, fontSize: 11, fontWeight: 600 }}>Retry</button>
        </div>
      )}
      {voiceError && (
        <div role="alert" style={{ background: "var(--badge-danger-bg)", border: "1px solid var(--accent-red)", borderRadius: 6, padding: "4px 8px", marginBottom: 6, fontSize: 11, color: "var(--accent-red)" }}>
          Voice error: {voiceError}
        </div>
      )}
      {attachmentDisabled && (
        <div
          role="status"
          aria-live="polite"
          className="composer-panel-notice"
          style={{ background: "var(--badge-info-bg)", border: "1px solid var(--accent-blue)", borderRadius: 6, padding: "6px 12px", marginBottom: 8, fontSize: 12, color: "var(--text-secondary)" }}
        >
          Opening a conversation. File attachments will be available when ready.
        </div>
      )}
      {isPanel && (
        <div role="status" className="composer-panel-notice" style={{ background: "var(--badge-info-bg)", border: "1px solid var(--accent-blue)", borderRadius: 6, padding: "6px 12px", marginBottom: 8, fontSize: 12, color: "var(--text-secondary)" }}>
          {panelDisabledMessage}
        </div>
      )}

      {/* Claude-grade Rounded Bordered Box */}
      <div
        className="staff-composer__box"
        data-testid="composer-box"
        onClick={(e) => {
          if (!disabled && !isPanel && !(e.target as HTMLElement | null)?.closest("button, input, select, label, a")) {
            textareaRef.current?.focus();
          }
        }}
      >
        {attachments.length > 0 && (
          <div className="staff-composer__attachments" aria-label="Staged attachments">
            {attachments.map((att) => (
              <span key={att.id} className={`staff-composer__chip staff-composer__chip--${att.status}`} data-testid={`composer-attachment-${att.filename}`}>
                <span className="staff-composer__chip-filename">{att.filename}</span>
                <button type="button" className="staff-composer__chip-remove" aria-label={`Remove ${att.filename}`} onClick={() => setAttachments((p) => p.filter((x) => x.id !== att.id))}>Remove</button>
                <span className="staff-composer__chip-meta">
                  <span className="staff-composer__chip-badge">{sensitivityLabel(att.sensitivity)}</span>
                </span>
                <span className={`staff-composer__chip-status${att.error ? " staff-composer__chip-status--error" : ""}`} role="status" aria-live="polite">
                  {attachmentStatusLabel(att.status)}{att.error ? `: ${att.error}` : ""}
                </span>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={text}
          disabled={disabled || isPanel || sendState === "sending"}
          onChange={(e) => {
            setText(e.target.value);
            saveDraft(threadId, e.target.value);
            setIdempotencyKey(null);
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
          <div className="staff-composer__footer-left">
            <input ref={fileInputRef} type="file" multiple tabIndex={-1} className="staff-composer__file-input" data-testid="composer-file-input" aria-label="Upload files to conversation" onChange={(e) => void stageFiles(e.target.files)} disabled={disabled || isPanel || attachmentDisabled || sendState === "sending"} />
            <select aria-label="Attachment sensitivity" data-testid="composer-sensitivity-select" className="staff-composer__sensitivity" value={sensitivity} onChange={(e) => setSensitivity(e.target.value as IntakeSensitivity)} disabled={disabled || isPanel || attachmentDisabled || sendState === "sending"}>
              <option value="confidential_work">Confidential work</option>
              <option value="public_reference">Library reference</option>
              <option value="vault_sensitive">Vault sensitive</option>
            </select>
            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={disabled || isPanel || attachmentDisabled || sendState === "sending"} title="Add files or context" aria-label="Add files or context" data-testid="composer-attach-btn" className="staff-composer__attach-btn">+</button>
          </div>
          {/* Voice Input Button */}
          <button
            type="button"
            aria-label={
              !voice.available
                ? "Voice input not supported"
                : voice.transcribing
                  ? "Transcribing voice input"
                  : voice.recording
                    ? "Stop voice input"
                    : "Voice input"
            }
            aria-pressed={voice.recording}
            onClick={voice.available ? voice.toggle : undefined}
            disabled={disabled || isPanel || sendState === "sending" || !voice.available || voice.transcribing}
            title={
              !voice.available
                ? "Voice input is not supported in this browser"
                : voice.engine === "local-whisper"
                  ? "Local Whisper voice input (Ctrl+Shift+V)"
                  : "Voice input (cloud fallback, Ctrl+Shift+V)"
            }
            className={`staff-composer__voice-btn ${voice.recording ? "staff-composer__voice-btn--recording" : ""}`}
            style={{
              cursor: voice.available && !isPanel ? "pointer" : "not-allowed",
              opacity: voice.available && !isPanel ? 1 : 0.4,
            }}
          >
            {voice.transcribing ? "…" : voice.recording ? "■" : <MicGlyph size={16} />}
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
