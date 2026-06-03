/**
 * EventLog — scrollable, virtualized, append-only fleet history (issue #863).
 *
 * Replaces the old screen-covering pop-up model with a durable, filterable
 * history panel. Renders:
 *   - a severity filter (all / info / warning / critical);
 *   - a windowed (virtualized) scroll region so a 500-event ring buffer stays
 *     cheap to render — only the visible slice is in the DOM;
 *   - per-row severity chips + relative-friendly absolute timestamps + node tag;
 *   - a visually-hidden `aria-live="polite"` region announcing the newest
 *     critical event to screen readers (a11y requirement).
 *
 * Presentation only: events are supplied by the host (which owns fetch + merge +
 * persistence via lib/fleetEvents). Pure styling via design tokens.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  EventSeverity,
  FleetEvent,
} from "../lib/fleetEvents";
import { filterBySeverity } from "../lib/fleetEvents";

export interface EventLogProps {
  /** Events to render, expected newest-first (the host merges them so). */
  events: ReadonlyArray<FleetEvent>;
  /** Optional fixed viewport height in px (default 360). */
  height?: number;
  /** Injectable clock for deterministic tests. */
  now?: () => number;
}

type SeverityFilter = EventSeverity | "all";

const ROW_HEIGHT = 56; // px — fixed-height rows make windowing exact.
const OVERSCAN = 4;
const VIRTUALIZE_THRESHOLD = 30;

const FILTERS: ReadonlyArray<{ id: SeverityFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "critical", label: "Critical" },
  { id: "warning", label: "Warning" },
  { id: "info", label: "Info" },
];

function formatTs(ts: number): string {
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return String(ts);
  }
}

export function EventLog({ events, height = 360 }: EventLogProps) {
  const [filter, setFilter] = useState<SeverityFilter>("all");
  const containerRef = useRef<HTMLDivElement>(null);
  const topSpacerRef = useRef<HTMLLIElement>(null);
  const bottomSpacerRef = useRef<HTMLLIElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(height);

  const filtered = useMemo(
    () => filterBySeverity(events, filter),
    [events, filter],
  );

  // a11y: announce the newest *critical* event to screen readers. Keyed off the
  // event key so the same event isn't re-announced on every render.
  const newestCritical = useMemo(
    () => events.find((e) => e.severity === "critical"),
    [events],
  );
  const liveMessage = newestCritical
    ? `Critical: ${newestCritical.title}. ${newestCritical.detail}`
    : "";

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.style.height = `${height}px`;
    setViewport(el.clientHeight || height);
  }, [height]);

  const useVirtual = filtered.length > VIRTUALIZE_THRESHOLD;
  const totalHeight = filtered.length * ROW_HEIGHT;

  let startIdx = 0;
  let endIdx = filtered.length;
  if (useVirtual) {
    startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visibleCount = Math.ceil(viewport / ROW_HEIGHT) + OVERSCAN * 2;
    endIdx = Math.min(filtered.length, startIdx + visibleCount);
  }
  const visible = filtered.slice(startIdx, endIdx);
  const padTop = startIdx * ROW_HEIGHT;
  const padBottom = (filtered.length - endIdx) * ROW_HEIGHT;

  useEffect(() => {
    if (topSpacerRef.current) topSpacerRef.current.style.height = `${padTop}px`;
    if (bottomSpacerRef.current) bottomSpacerRef.current.style.height = `${padBottom}px`;
  }, [padTop, padBottom]);

  return (
    <div className="event-log" data-touch-primitive="EventLog">
      <div className="event-log__toolbar">
        <div
          className="event-log__filters"
          role="group"
          aria-label="Filter events by severity"
        >
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`event-log__filter event-log__filter--${f.id}${
                filter === f.id ? " event-log__filter--active" : ""
              }`}
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <span className="event-log__count">
          {filtered.length} event{filtered.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* Visually-hidden live region for new critical events. */}
      <div
        aria-live="polite"
        aria-atomic="true"
        data-testid="event-log-live"
        className="visually-hidden"
      >
        {liveMessage}
      </div>

      <div
        ref={containerRef}
        className="event-log__scroll"
        role="log"
        aria-label="Fleet event history"
        tabIndex={0}
        onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      >
        {filtered.length === 0 ? (
          <p className="event-log__empty">
            No events to show.
          </p>
        ) : (
          <ul className="event-log__list">
            {padTop > 0 ? (
              <li ref={topSpacerRef} className="event-log__spacer" aria-hidden="true" />
            ) : null}
            {visible.map((e) => (
              <li
                key={`${e.ts}-${e.kind}-${e.node ?? ""}-${e.title}`}
                className={`event-log__row event-log__row--${e.severity}`}
              >
                <span
                  className={`event-log__chip event-log__chip--${e.severity}`}
                >
                  {e.severity}
                </span>
                <div className="event-log__body">
                  <div className="event-log__title">
                    {e.title}
                    {e.node ? (
                      <span className="event-log__node">
                        {e.node}
                      </span>
                    ) : null}
                  </div>
                  {e.detail ? (
                    <div className="event-log__detail">
                      {e.detail}
                    </div>
                  ) : null}
                </div>
                <time
                  className="event-log__ts"
                  dateTime={new Date(e.ts).toISOString()}
                >
                  {formatTs(e.ts)}
                </time>
              </li>
            ))}
            {padBottom > 0 ? (
              <li ref={bottomSpacerRef} className="event-log__spacer" aria-hidden="true" />
            ) : null}
          </ul>
        )}
      </div>
      {useVirtual ? <div className="event-log__virtual-size" aria-hidden="true">{totalHeight}</div> : null}
    </div>
  );
}
