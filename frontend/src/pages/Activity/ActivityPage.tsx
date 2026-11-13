/**
 * ActivityPage.tsx — chat-style Activity page (#2864) + All work fleet feed (#2865).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState } from "../../primitives/EmptyState";
import { conflictLabelsForEvent, fetchActivityContext, type ConflictSummary } from "./activityContextApi";
import {
  fetchFleetEvents,
  fetchSessionEvents,
  fetchSessions,
  groupSessions,
  sessionKeyOf,
  sortEvents,
  type ActivityEnvelope,
  type SessionSummary,
} from "./activityApi";
import {
  ALL_WORK_CHANNEL,
  eventMatchesFilters,
  readActivityFiltersFromUrl,
  readSessionFromUrl,
  syncActivityUrl,
  type ActivityFilters,
} from "./activityUrlState";
import { ActivityVirtualScrollback } from "./ActivityVirtualScrollback";

export const ACTIVITY_POLL_MS = 30_000;

function initialSelection(): string {
  return readSessionFromUrl() ?? ALL_WORK_CHANNEL;
}

export function ActivityPage(): React.ReactElement {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>(() => initialSelection());
  const [filters, setFilters] = useState<ActivityFilters>(() => readActivityFiltersFromUrl());
  const [events, setEvents] = useState<Map<string, ActivityEnvelope>>(new Map());
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<ConflictSummary[]>([]);

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
    () => (selected === ALL_WORK_CHANNEL ? null : sessions?.find((s) => sessionKeyOf(s) === selected) ?? null),
    [sessions, selected],
  );

  const apiRepo = filters.repo.trim() || undefined;
  const apiNode = filters.node.trim() || undefined;

  useEffect(() => {
    setEvents(new Map());
    setEventsError(null);
    const load = () => {
      const promise =
        selected === ALL_WORK_CHANNEL
          ? fetchFleetEvents({ repo: apiRepo, node: apiNode })
          : selectedSummary
            ? fetchSessionEvents(selectedSummary.session_id)
            : Promise.resolve({ items: [], next_cursor: null });
      promise
        .then((page) => {
          setEvents((prev) => {
            const next = new Map(prev);
            for (const event of page.items) {
              if (selected === ALL_WORK_CHANNEL && !eventMatchesFilters(event, filters)) continue;
              next.set(event.event_id, event);
            }
            return next;
          });
          setEventsError(null);
        })
        .catch((e: unknown) => setEventsError(e instanceof Error ? e.message : String(e)));
    };
    load();
    const id = window.setInterval(load, ACTIVITY_POLL_MS);
    return () => window.clearInterval(id);
  }, [selected, selectedSummary, filters, apiRepo, apiNode]);

  useEffect(() => {
    fetchActivityContext(apiRepo)
      .then((ctx) => setConflicts(ctx.conflicts ?? []))
      .catch(() => setConflicts([]));
  }, [apiRepo]);

  const select = useCallback(
    (key: string) => {
      setSelected(key);
      syncActivityUrl(filters, key);
    },
    [filters],
  );

  const updateFilter = useCallback(
    (field: keyof ActivityFilters, value: string) => {
      setFilters((prev) => {
        const next = { ...prev, [field]: value };
        syncActivityUrl(next, selected);
        return next;
      });
    },
    [selected],
  );

  const groups = useMemo(() => groupSessions(sessions ?? []), [sessions]);
  const orderedEvents = useMemo(() => sortEvents(events.values()), [events]);
  const scrollbackRows = useMemo(
    () =>
      orderedEvents.map((event) => ({
        event,
        conflictLabels: conflictLabelsForEvent(event, conflicts),
      })),
    [orderedEvents, conflicts],
  );

  if (sessionsError) {
    return (
      <div className="activity-page">
        <p className="events-tab__degraded">Activity sessions unavailable: {sessionsError}</p>
      </div>
    );
  }

  const filterFields: { id: keyof ActivityFilters; label: string; inputMode?: "numeric" }[] = [
    { id: "repo", label: "Repo" },
    { id: "issue", label: "Issue", inputMode: "numeric" },
    { id: "agent", label: "Agent" },
    { id: "node", label: "Node" },
  ];

  return (
    <div className="activity-page">
      <h1>Activity</h1>
      <p>Every agent/session checked into the fleet (#2856); read-only.</p>
      <div className="activity-page__filters" aria-label="Activity filters">
        {filterFields.map(({ id, label, inputMode }) => (
          <label key={id}>
            {label}
            <input
              value={filters[id]}
              inputMode={inputMode}
              onChange={(e) => updateFilter(id, e.target.value)}
            />
          </label>
        ))}
      </div>
      <div className="activity-page__layout">
        <nav className="activity-page__sessions" aria-label="Sessions by node and status">
          <section className="activity-page__all-work">
            <button
              type="button"
              aria-pressed={selected === ALL_WORK_CHANNEL}
              onClick={() => select(ALL_WORK_CHANNEL)}
            >
              All work
            </button>
          </section>
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
          {selected === ALL_WORK_CHANNEL ? (
            <>
              <h2>All work</h2>
              {eventsError && <p className="events-tab__degraded">Activity feed unavailable: {eventsError}</p>}
              {scrollbackRows.length === 0 ? (
                <EmptyState title="No events" description="Adjust filters or wait for fleet activity." />
              ) : (
                <ActivityVirtualScrollback rows={scrollbackRows} />
              )}
            </>
          ) : !selectedSummary ? (
            <EmptyState title="No session selected" description="Pick All work or a session from the left." />
          ) : (
            <>
              <h2>
                {selectedSummary.session_id} ({selectedSummary.status})
              </h2>
              {eventsError && <p className="events-tab__degraded">Activity feed unavailable: {eventsError}</p>}
              <ActivityVirtualScrollback rows={scrollbackRows} />
            </>
          )}
        </main>
      </div>
    </div>
  );
}

export default ActivityPage;
