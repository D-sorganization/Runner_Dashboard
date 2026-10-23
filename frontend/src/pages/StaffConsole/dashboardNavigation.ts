import type { ThreadMessage } from "./threadTypes";

const consumedMessageIds = new Set<string>();
const EVENT_NAME = "runner-dashboard:navigate";

/** Dispatch a shell navigation event only for a successful, approved action result. */
export function dispatchDashboardNavigation(message: ThreadMessage): void {
  if (
    typeof window === "undefined" ||
    message.kind !== "action_result" ||
    message.meta?.action !== "dashboard.navigate" ||
    message.meta?.success !== true
  ) {
    return;
  }
  const result = message.meta.result;
  const tabId =
    result && typeof result === "object"
      ? (result as Record<string, unknown>).navigate_to
      : undefined;
  if (typeof tabId !== "string" || !tabId) return;

  const key = `runner-dashboard:navigate:${JSON.stringify([message.thread_id, message.id])}`;
  if (consumedMessageIds.has(key)) return;
  try {
    if (window.sessionStorage.getItem(key)) {
      consumedMessageIds.add(key);
      return;
    }
    window.sessionStorage.setItem(key, "1");
  } catch {
    // The in-memory guard still prevents duplicate SSE replay in this session.
  }
  consumedMessageIds.add(key);
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { tabId } }));
}
