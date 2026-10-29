import { useCallback, useEffect, useState } from "react";

interface MobileThreadTarget {
  id: string;
}

interface MobileHomeSendNavigationOptions {
  activeThread: MobileThreadTarget | null;
  isHomeView: () => boolean;
  onOpenThread?: (threadId: string) => void;
  setThreadView: () => void;
  /** Role whose thread the home composer just opened; stamped into the pushed URL. */
  role: string;
}

export function pushThreadUrl(roleName: string, threadId: string): void {
  if (typeof window === "undefined") return;
  const query = `?role=${encodeURIComponent(roleName)}&thread=${encodeURIComponent(threadId)}`;
  window.history.pushState(null, "", query);
}

export function selectMobileThread(
  threadId: string,
  roleName: string,
  onThreadSelected?: (threadId: string, role: string) => void,
  onOpenThread?: (threadId: string) => void,
): void {
  if (onThreadSelected) {
    onThreadSelected(threadId, roleName);
    return;
  }
  onOpenThread?.(threadId);
  pushThreadUrl(roleName, threadId);
}

export async function startNewMobileConversation(
  createConversation: () => Promise<MobileThreadTarget | null>,
  roleName: string,
  onThreadSelected?: (threadId: string, role: string) => void,
  onOpenThread?: (threadId: string) => void,
): Promise<MobileThreadTarget | null> {
  const thread = await createConversation();
  if (!thread) return null;
  selectMobileThread(thread.id, roleName, onThreadSelected, onOpenThread);
  return thread;
}

/** Reveal the new conversation after a successful chat-first home send. */
export function useMobileHomeSendNavigation({
  activeThread,
  isHomeView,
  onOpenThread,
  setThreadView,
  role,
}: MobileHomeSendNavigationOptions): () => void {
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!pending || !activeThread) return;
    setPending(false);
    if (!isHomeView()) return;
    setThreadView();
    pushThreadUrl(role, activeThread.id);
    onOpenThread?.(activeThread.id);
  }, [activeThread, isHomeView, onOpenThread, pending, setThreadView, role]);

  return useCallback(() => setPending(true), []);
}