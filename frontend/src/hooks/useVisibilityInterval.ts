/**
 * useVisibilityInterval — runs `callback` on a fixed interval, pausing while
 * `document.hidden` is true and firing an immediate refetch when the tab
 * becomes visible again (#3750). For pages still on manual
 * `useState`/`fetch` polling rather than TanStack Query's own
 * `refetchIntervalInBackground: false` (see `usePollingQueries.ts`).
 */
import { useEffect, useRef } from "react";

export function useVisibilityInterval(callback: () => void, intervalMs: number): void {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (typeof document === "undefined") return;

    let id: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (id !== null) return;
      id = setInterval(() => callbackRef.current(), intervalMs);
    };
    const stop = () => {
      if (id === null) return;
      clearInterval(id);
      id = null;
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        stop();
        return;
      }
      callbackRef.current();
      start();
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [intervalMs]);
}
