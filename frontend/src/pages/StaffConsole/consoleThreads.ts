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

/**
 * Precondition: `role` is a non-empty role name.
 * Postcondition: the returned thread includes `role` as a participant.
 * Backend failures propagate; no thread is ever invented client-side.
 */
export async function resolveRoleThread(role: string, roleTitle: string, api: ThreadApi): Promise<ThreadInfo> {
  const name = role.trim();
  if (!name) throw new Error("resolveRoleThread: role name must be non-empty");

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
}
