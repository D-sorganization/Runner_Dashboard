/**
 * useFleetEvents — fetch + merge + persist the fleet event feed (issue #863).
 *
 * Polls `GET /api/events`, merges the freshly-fetched events into the retained
 * (localStorage-persisted) history via the pure `mergeEvents`, and exposes the
 * newest-first list plus the event-derived AlertsCenter alerts. Both the
 * Overview AlarmPanel and the dedicated Events tab consume this single hook so
 * they never diverge (DRY).
 *
 * Persistence makes the log survive reloads (a hard requirement); polling
 * pauses when the tab is hidden, mirroring the other polling hooks.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { FleetAlert } from "../lib/fleetAlerts";
import type { FleetEvent } from "../lib/fleetEvents";
import {
  eventsToAlerts,
  loadEvents,
  mergeEvents,
  saveEvents,
} from "../lib/fleetEvents";

export interface UseFleetEventsResult {
  /** Retained events, newest-first. */
  events: FleetEvent[];
  /** Event-derived alerts for the AlertsCenter pill / AlarmPanel. */
  alerts: FleetAlert[];
  /** True while the first fetch is in flight. */
  loading: boolean;
  /** Last fetch error message, if any. */
  error: string | null;
  /** Imperative refetch (e.g. a manual refresh button). */
  refetch: () => void;
}

const DEFAULT_INTERVAL_MS = 20_000;

export function useFleetEvents(
  intervalMs: number = DEFAULT_INTERVAL_MS,
): UseFleetEventsResult {
  // Seed from persisted history so the log is populated before the first fetch.
  const [events, setEvents] = useState<FleetEvent[]>(() => loadEvents());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const fetchOnce = useCallback(async (signal?: AbortSignal) => {
    try {
      const resp = await api.events.list({ limit: 200, signal });
      if (!mounted.current) return;
      setEvents((prev) => {
        const merged = mergeEvents(prev, resp.events);
        saveEvents(merged);
        return merged;
      });
      setError(null);
    } catch (e) {
      if (!mounted.current) return;
      // AbortError on unmount/refetch is expected — don't surface it.
      if (e instanceof DOMException && e.name === "AbortError") return;
      setError(e instanceof Error ? e.message : "Failed to load events");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  const refetch = useCallback(() => {
    void fetchOnce();
  }, [fetchOnce]);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void fetchOnce(controller.signal);

    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) {
        timer = setInterval(() => void fetchOnce(), intervalMs);
      }
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else {
        void fetchOnce();
        start();
      }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      mounted.current = false;
      stop();
      controller.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [fetchOnce, intervalMs]);

  return {
    events,
    alerts: eventsToAlerts(events),
    loading,
    error,
    refetch,
  };
}
