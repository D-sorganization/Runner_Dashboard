/**
 * ActivityPage.tsx — chat-style Activity page (#2864, epic #2856 slice U1).
 *
 * Left panel: every checked-in session (#2860's `GET /activity/sessions`
 * projection), grouped by node then status with counts; selecting one is
 * URL-addressable (`?session=<source>:<session_id>`, the same read-on-mount
 * + `replaceState`-on-select convention as `Desktop.tsx`/`StaffPage.tsx`).
 * Main panel polls that session's persisted events (`GET /activity`) and
 * renders them as scrollback, merged by `event_id` so a slow poll or
 * reconnect never duplicates a row. Gap records (overflow/retention/capture)
 * render inline, flagged, never implying an unpersisted chunk was accepted.
 * Read-only: no composer, no mutating call.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState } from "../../primitives/EmptyState";
import {
  fetchSessionEvents,
  fetchSessions,
  groupSessions,
  isGapEvent,
  sessionKeyOf,
  sortEvents,
  type ActivityEnvelope,
  type SessionSummary,
} from "./activityApi";

export const ACTIVITY_POLL_MS = 30_000;

function selectedSessionFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("session") || null;
}

function syncSelectionToUrl(key: string | null): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  if (key) params.set("session", key);
  else params.delete("session");
  const qs = params.toString();
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`);
}

export function ActivityPage(): React.ReactElement {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(() => selectedSessionFromUrl());
  const [events, setEvents] = useState<Map<string, ActivityEnvelope>>(new Map());
  const [eventsError, setEventsError] = useState<string | null>(null);

  const loadSessions = useCallback(() => {
    fetchSessions()
      .then((page) => {
        setSessions(page.items);
        setSessionsError(null);
      })
      .catch((e: unknown) => setSessionsError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    loadSessions();
    const id = window.setInterval(loadSessions, ACTIVITY_POLL_MS);
    return () => window.clearInterval(id);
  }, [loadSessions]);

  const selectedSummary = useMemo(
    () => sessions?.find((s) => sessionKeyOf(s) === selected) ?? null,
    [sessions, selected],
  );

  useEffect(() => {
    setEvents(new Map());
    setEventsError(null);
    if (!selectedSummary) return undefined;
    const load = () => {
      fetchSessionEvents(selectedSummary.session_id)
        .then((page) => {
          setEvents((prev) => {
            const next = new Map(prev);
            for (const event of page.items) next.set(event.event_id, event);
            return next;
          });
          setEventsError(null);
        })
        .catch((e: unknown) => setEventsError(e instanceof Error ? e.message : String(e)));
    };
    load();
    const id = window.setInterval(load, ACTIVITY_POLL_MS);
    return () => window.clearInterval(id);
  }, [selectedSummary]);

  const select = useCallback((key: string) => {
    setSelected(key);
    syncSelectionToUrl(key);
  }, []);

  const groups = useMemo(() => groupSessions(sessions ?? []), [sessions]);
  const orderedEvents = useMemo(() => sortEvents(events.values()), [events]);

  if (sessionsError) {
    return (
      <div className="activity-page">
        <p className="events-tab__degraded">Activity sessions unavailable: {sessionsError}</p>
      </div>
    );
  }

  return (
    <div className="activity-page">
      <h1>Activity</h1>
      <p>Every agent/session checked into the fleet (#2856); read-only.</p>
      <div className="activity-page__layout">
        <nav className="activity-page__sessions" aria-label="Sessions by node and status">
          {sessions === null ? (
            <p>Loading sessions…</p>
          ) : sessions.length === 0 ? (
            <EmptyState
              title="No live sessions"
              description="Sessions appear once producers report activity events."
            />
          ) : (
            [...groups.entries()].map(([node, byStatus]) => (
              <section key={node}>
                <h2>{node}</h2>
                <ul role="list">
                  {[...byStatus.entries()].map(([status, items]) => (
                    <li key={status}>
                      <span className="activity-page__status-label">
                        {status} ({items.length})
                      </span>
                      <ul role="list">
                        {items.map((item) => {
                          const key = sessionKeyOf(item);
                          return (
                            <li key={key}>
                              <button type="button" aria-pressed={key === selected} onClick={() => select(key)}>
                                {item.session_id}
                                {item.repo ? ` — ${item.repo}` : ""}
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </nav>

        <main className="activity-page__transcript" aria-live="polite">
          {!selectedSummary ? (
            <EmptyState
              title="No session selected"
              description="Pick a session from the left to view its activity."
            />
          ) : (
            <>
              <h2>
                {selectedSummary.session_id} ({selectedSummary.status})
              </h2>
              {eventsError && <p className="events-tab__degraded">Activity feed unavailable: {eventsError}</p>}
              <ul role="list" className="activity-page__scrollback">
                {orderedEvents.map((event) => (
                  <li key={event.event_id} className={isGapEvent(event) ? "activity-page__gap" : undefined}>
                    <strong>{event.kind}</strong> <time dateTime={event.ts}>{event.ts}</time>
                    {isGapEvent(event) && <span role="status"> — gap</span>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

export default ActivityPage;
