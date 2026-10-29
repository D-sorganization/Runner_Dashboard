import { useEffect, useRef, useState } from "react";
import type { ThreadInfo } from "./threadTypes";

export interface MobileUrlParams {
  threadId: string | null;
  roleName: string | null;
  tab: string | null;
}

export function parseMobileUrlParams(): MobileUrlParams {
  if (typeof window === "undefined") return { threadId: null, roleName: null, tab: null };
  const p = new URLSearchParams(window.location.search);
  const tabParam = p.get("tab") || (p.get("inbox") ? "inbox" : p.get("runs") ? "runs" : null);
  return {
    threadId: p.get("thread"),
    roleName: p.get("role"),
    tab: tabParam,
  };
}

/**
 * Owns the thread id a sibling surface (sidebar tap, `?thread=` deep link)
 * asked for but this console has not yet locally confirmed. Must be called
 * before useStaffConsole so its initial value can seed that hook's
 * initialThreadId fetch-and-authorize path; never synthesize a thread object
 * for this id locally (#2663 fail-closed contract from #2652).
 */
export function usePendingThreadId() {
  return useState<string | null>(() => parseMobileUrlParams().threadId);
}

export interface UseMobileThreadUrlSyncArgs {
  pendingThreadId: string | null;
  setPendingThreadId: (id: string | null) => void;
  activeThread: ThreadInfo | null;
  initialThread: ThreadInfo | undefined;
  openRole: (roleName: string) => Promise<ThreadInfo | null>;
  openThread: (thread: ThreadInfo, roleName?: string) => void;
  setView: (view: "thread" | "inbox" | "runs") => void;
}

/**
 * Re-syncs mobile's view/selection from the URL on mount, on native
 * `popstate`, and after every render (SPA navigation via react-router's
 * `history.pushState` fires neither, so a sibling surface's selection would
 * otherwise never reach this mounted console).
 */
export function useMobileThreadUrlSync({
  pendingThreadId,
  setPendingThreadId,
  activeThread,
  initialThread,
  openRole,
  openThread,
  setView,
}: UseMobileThreadUrlSyncArgs) {
  const syncWithUrl = () => {
    const { threadId, roleName, tab } = parseMobileUrlParams();
    if (threadId) {
      // A thread already loaded (e.g. our own selection echoed back through
      // the shell's URL) needs no fetch and no lock.
      if (threadId !== activeThread?.id) setPendingThreadId(threadId);
      setView("thread");
      return;
    }
    if (roleName) {
      if (initialThread?.participants.includes(roleName)) {
        openThread(initialThread, roleName);
        setView("thread");
      } else {
        void openRole(roleName).then((thread) => thread && setView("thread"));
      }
      return;
    }
    if (tab === "inbox") setView("inbox");
    else if (tab === "runs") setView("runs");
  };
  const syncWithUrlRef = useRef(syncWithUrl);
  syncWithUrlRef.current = syncWithUrl;

  useEffect(() => {
    const onPopState = () => syncWithUrlRef.current();
    onPopState();
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const lastSyncedSearchRef = useRef(
    typeof window !== "undefined" ? window.location.search : "",
  );
  useEffect(() => {
    const currentSearch = typeof window !== "undefined" ? window.location.search : "";
    if (currentSearch === lastSyncedSearchRef.current) return;
    lastSyncedSearchRef.current = currentSearch;
    syncWithUrlRef.current();
  });

  return Boolean(pendingThreadId) && activeThread?.id !== pendingThreadId;
}
