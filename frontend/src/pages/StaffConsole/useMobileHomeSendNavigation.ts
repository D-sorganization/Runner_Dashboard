import { useCallback, useEffect, useState } from "react";

interface MobileThreadTarget {
  id: string;
}

interface MobileHomeSendNavigationOptions {
  activeThread: MobileThreadTarget | null;
  isHomeView: () => boolean;
  onOpenThread?: (threadId: string) => void;
  setThreadView: () => void;
}

export function pushThreadUrl(roleName: string, threadId: string): void {
  if (typeof window === "undefined") return;
  const query = `?role=${encodeURIComponent(roleName)}&thread=${encodeURIComponent(threadId)}`;
  window.history.pushState(null, "", query);
}

/** Reveal the new conversation after a successful chat-first home send. */
export function useMobileHomeSendNavigation({
  activeThread,
  isHomeView,
  onOpenThread,
  setThreadView,
}: MobileHomeSendNavigationOptions): () => void {
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!pending || !activeThread) return;
    setPending(false);
    if (!isHomeView()) return;
    setThreadView();
    pushThreadUrl("barb", activeThread.id);
    onOpenThread?.(activeThread.id);
  }, [activeThread, isHomeView, onOpenThread, pending, setThreadView]);

  return useCallback(() => setPending(true), []);
}