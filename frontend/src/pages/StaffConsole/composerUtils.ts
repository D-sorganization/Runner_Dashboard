/**
 * composerUtils.ts — Helpers for Staff Console Composer: mentions parsing,
 * slash command triggers, idempotency key generation, and draft persistence.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350).
 */
import { SLASH_COMMANDS, type SlashCommand } from "./threadTypes";
import type { StaffRoleItem } from "./types";

const DRAFT_PREFIX = "staff-console:draft:";

/** Extracts unique role names mentioned via @role */
export function extractMentions(text: string): string[] {
  if (!text) return [];
  const matches = text.match(/@([a-zA-Z0-9_-]+)/g);
  if (!matches) return [];
  const unique = new Set(matches.map((m) => m.slice(1).toLowerCase()));
  return Array.from(unique);
}

export interface MentionQuery {
  query: string;
  startIndex: number;
}

/** Determines if caret is currently inside an @mention token */
export function getMentionQuery(text: string, caretIndex: number): MentionQuery | null {
  if (caretIndex < 0 || caretIndex > text.length) return null;

  const textBeforeCaret = text.slice(0, caretIndex);
  const lastAt = textBeforeCaret.lastIndexOf("@");
  if (lastAt === -1) return null;

  // The @ must be either at the start or preceded by whitespace
  if (lastAt > 0 && !/\s/.test(textBeforeCaret[lastAt - 1])) {
    return null;
  }

  const token = textBeforeCaret.slice(lastAt + 1);
  // No whitespace allowed inside mention token
  if (/\s/.test(token)) {
    return null;
  }

  return {
    query: token.toLowerCase(),
    startIndex: lastAt,
  };
}

export interface SlashQuery {
  query: string;
}

/** Determines if input starts with a slash command token being typed */
export function getSlashCommandQuery(text: string): SlashQuery | null {
  if (!text.startsWith("/")) return null;

  const firstSpace = text.indexOf(" ");
  // If there's already a space, command name is already entered
  if (firstSpace !== -1) return null;

  return {
    query: text.slice(1).toLowerCase(),
  };
}

/** Filters slash commands matching user query */
export function filterSlashCommands(query: string): SlashCommand[] {
  if (!query) return [...SLASH_COMMANDS];
  const q = query.toLowerCase();
  return SLASH_COMMANDS.filter(
    (cmd) => cmd.name.toLowerCase().includes(q) || cmd.description.toLowerCase().includes(q)
  );
}

/** Filters staff roles for mention autocomplete */
export function filterMentionRoles(query: string, roles: StaffRoleItem[] = []): StaffRoleItem[] {
  if (!query) return roles;
  const q = query.toLowerCase();
  return roles.filter(
    (role) =>
      role.name.toLowerCase().includes(q) ||
      (role.title && role.title.toLowerCase().includes(q))
  );
}

/** Generates a cryptographically random or high-entropy Idempotency-Key */
export function generateIdempotencyKey(threadId: string): string {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).substring(2, 10);
  return `key-${threadId.slice(0, 8)}-${ts}-${rand}`;
}

/** Loads saved thread draft from localStorage */
export function getDraft(threadId: string): string {
  if (!threadId) return "";
  try {
    return localStorage.getItem(`${DRAFT_PREFIX}${threadId}`) || "";
  } catch {
    return "";
  }
}

/** Saves draft for a specific thread in localStorage */
export function saveDraft(threadId: string, text: string): void {
  if (!threadId) return;
  try {
    if (!text.trim()) {
      localStorage.removeItem(`${DRAFT_PREFIX}${threadId}`);
    } else {
      localStorage.setItem(`${DRAFT_PREFIX}${threadId}`, text);
    }
  } catch {
    // ignore quota errors
  }
}

/** Clears draft for a thread upon successful send */
export function clearDraft(threadId: string): void {
  if (!threadId) return;
  try {
    localStorage.removeItem(`${DRAFT_PREFIX}${threadId}`);
  } catch {
    // ignore
  }
}

import type { ComposerAttachment, IntakeSensitivity } from "./threadTypes";

export async function uploadIntakeFile(
  file: File,
  sensitivity: IntakeSensitivity,
  threadId: string,
): Promise<ComposerAttachment> {
  const id = `att-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const base: ComposerAttachment = {
    id,
    filename: file.name,
    sizeBytes: file.size,
    mimeType: file.type || "application/octet-stream",
    sensitivity,
    status: "attached",
  };
  try {
    let b64 = "";
    if (typeof file.arrayBuffer === "function") {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let bin = "";
      for (let i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
      b64 = typeof btoa === "function" ? btoa(bin) : "";
    }
    const resp = await fetch("/api/v1/library/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({
        filename: file.name,
        content_base64: b64,
        mime_type: base.mimeType,
        size_bytes: file.size,
        sensitivity,
        thread_id: threadId,
      }),
    });
    if (resp && resp.ok) {
      const data = (await resp.json()) as { id?: number; sha256?: string; sensitivity?: IntakeSensitivity };
      return { ...base, intakeId: data.id, sha256: data.sha256, sensitivity: data.sensitivity ?? sensitivity };
    }
    if (resp && !resp.ok) {
      const recovery = resp.status === 503
        ? " The service may be temporarily unavailable; remove and reattach the file to retry."
        : " Remove and reattach the file to retry.";
      return { ...base, status: "rejected", error: `Upload failed (${resp.status}).${recovery}` };
    }
  } catch {
    return { ...base, status: "rejected", error: "Upload failed. Remove this file and choose it again to retry." };
  }
  return { ...base, status: "rejected", error: "Upload did not complete. Remove this file and choose it again to retry." };
}
