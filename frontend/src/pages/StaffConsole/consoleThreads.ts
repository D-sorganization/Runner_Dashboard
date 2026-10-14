/**
 * consoleThreads.ts — resolve a staff role to a real backend thread (#1446).
 *
 * Opening a role reuses the most recently active live thread the role takes
 * part in, or creates one. Barb gets `auto` threads (routed), every other
 * role gets `direct` threads. Thread ids always come from the backend.
 */
import type { ThreadInfo } from "./threadTypes";

/** The role that owns auto-routed conversations. */
export const AUTO_ROUTE_ROLE = "barb";

export type ConsoleThreadKind = "auto" | "direct";

/** The backend calls resolution needs; injected so tests stay hermetic. */
export interface ThreadApi {
  listThreads: (role: string) => Promise<ThreadInfo[]>;
  createThread: (body: { role: string; kind: ConsoleThreadKind; title: string }) => Promise<ThreadInfo>;
}

export function threadKindForRole(role: string): ConsoleThreadKind {
  return role === AUTO_ROUTE_ROLE ? "auto" : "direct";
}

function activityOf(thread: ThreadInfo): string {
  return thread.last_message_at || thread.updated_at || thread.created_at || "";
}

/** Most recently active, non-archived thread of the role's kind, or null. */
export function pickRoleThread(threads: readonly ThreadInfo[], role: string): ThreadInfo | null {
  const kind = threadKindForRole(role);
  const candidates = threads.filter(
    (t) => t.status !== "archived" && t.kind === kind && t.participants.includes(role),
  );
  if (candidates.length === 0) return null;
  return candidates.reduce((best, t) => (activityOf(t) > activityOf(best) ? t : best));
}

// Concurrent resolutions for the same role (e.g. a double-fired click) share
// one in-flight lookup/create instead of each racing to see "no existing
// thread" and creating a duplicate (issue: double POST /threads per click).
const inFlightByRole = new Map<string, Promise<ThreadInfo>>();

/**
 * Precondition: `role` is a non-empty role name.
 * Postcondition: the returned thread includes `role` as a participant.
 * Backend failures propagate; no thread is ever invented client-side.
 */
export async function resolveRoleThread(role: string, roleTitle: string, api: ThreadApi): Promise<ThreadInfo> {
  const name = role.trim();
  if (!name) throw new Error("resolveRoleThread: role name must be non-empty");

  const existingInFlight = inFlightByRole.get(name);
  if (existingInFlight) return existingInFlight;

  const resolution = (async () => {
    const existing = pickRoleThread(await api.listThreads(name), name);
    if (existing) return existing;

    const created = await api.createThread({
      role: name,
      kind: threadKindForRole(name),
      title: `Conversation with ${roleTitle || name}`,
    });
    if (!created?.participants?.includes(name)) {
      throw new Error(`resolveRoleThread: created thread ${created?.id ?? "?"} does not include role '${name}'`);
    }
    return created;
  })();

  inFlightByRole.set(name, resolution);
  try {
    return await resolution;
  } finally {
    inFlightByRole.delete(name);
  }
}

/**
 * Precondition: `role` is a non-empty role name.
 * Postcondition: the returned thread includes `role` as a participant.
 * Always creates a new thread for the role, bypassing `pickRoleThread` — used
 * by "New conversation" so a fresh topic never lands in an old thread whose
 * history and routing state carry over (#1775).
 */
export async function createFreshRoleThread(role: string, roleTitle: string, api: ThreadApi): Promise<ThreadInfo> {
  const name = role.trim();
  if (!name) throw new Error("createFreshRoleThread: role name must be non-empty");

  const created = await api.createThread({
    role: name,
    kind: threadKindForRole(name),
    title: `Conversation with ${roleTitle || name}`,
  });
  if (!created?.participants?.includes(name)) {
    throw new Error(`createFreshRoleThread: created thread ${created?.id ?? "?"} does not include role '${name}'`);
  }
  return created;
}

/**
 * Return all active/historical threads for a role, ordered by most recent activity.
 * Supports multi-mission discovery and selection without clobbering active state (#1806).
 */
export function listRoleThreads(threads: readonly ThreadInfo[], role: string): ThreadInfo[] {
  const kind = threadKindForRole(role);
  return threads
    .filter((t) => t.status !== "archived" && t.kind === kind && t.participants.includes(role))
    .sort((a, b) => (activityOf(b) > activityOf(a) ? 1 : activityOf(b) < activityOf(a) ? -1 : 0));
}

/**
 * Resume an older mission thread by id without overwriting other conversations (#1806).
 */
export function resumeMissionThread(threads: readonly ThreadInfo[], threadId: string): ThreadInfo | null {
  if (!threadId) return null;
  const match = threads.find((t) => t.id === threadId && t.status !== "archived");
  return match || null;
}

/**
 * Format a human-readable display label for a mission thread.
 */
export function getRoleMissionName(thread: ThreadInfo): string {
  if (thread.title && thread.title.trim()) {
    return thread.title.trim();
  }
  return `Conversation (${thread.id.slice(0, 8)})`;
}

// In-memory fallback for draft preservation when sessionStorage is unavailable.
const inMemoryDrafts = new Map<string, string>();

/**
 * Save an uncommitted input draft for a thread across navigation and reconnect (BR-13).
 */
export function saveThreadDraft(threadId: string, draft: string): void {
  const tid = threadId?.trim();
  if (!tid) return;
  inMemoryDrafts.set(tid, draft);
  try {
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.setItem(`staff_draft_${tid}`, draft);
    }
  } catch {
    // Ignore quota / access restrictions
  }
}

/**
 * Retrieve a preserved draft for a thread, or empty string.
 */
export function getThreadDraft(threadId: string): string {
  const tid = threadId?.trim();
  if (!tid) return "";
  if (inMemoryDrafts.has(tid)) {
    return inMemoryDrafts.get(tid) || "";
  }
  try {
    if (typeof sessionStorage !== "undefined") {
      const stored = sessionStorage.getItem(`staff_draft_${tid}`);
      if (stored !== null) {
        inMemoryDrafts.set(tid, stored);
        return stored;
      }
    }
  } catch {
    // Ignore
  }
  return "";
}

/**
 * Clear a preserved draft when the message is sent or explicitly discarded.
 */
export function clearThreadDraft(threadId: string): void {
  const tid = threadId?.trim();
  if (!tid) return;
  inMemoryDrafts.delete(tid);
  try {
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.removeItem(`staff_draft_${tid}`);
    }
  } catch {
    // Ignore
  }
}
