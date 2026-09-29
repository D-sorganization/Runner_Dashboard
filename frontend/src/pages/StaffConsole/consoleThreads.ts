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
