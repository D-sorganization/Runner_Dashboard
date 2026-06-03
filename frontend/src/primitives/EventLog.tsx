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
import type { CSSProperties } from "react";
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

const SEVERITY_COLOR: Record<EventSeverity, string> = {
  info: "var(--accent-blue)",
  warning: "var(--accent-yellow)",
  critical: "var(--accent-red)",
};

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

  return (
    <div className="event-log" data-touch-primitive="EventLog">
      <div className="event-log__toolbar" style={toolbarStyle}>
        <div
          role="group"
          aria-label="Filter events by severity"
          style={{ display: "flex", gap: 6 }}
        >
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`event-log__filter event-log__filter--${f.id}`}
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
              style={filterBtnStyle(filter === f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <span className="event-log__count" style={{ color: "var(--text-secondary)", fontSize: 12 }}>
          {filtered.length} event{filtered.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* Visually-hidden live region for new critical events. */}
      <div
        aria-live="polite"
        aria-atomic="true"
        data-testid="event-log-live"
        style={srOnlyStyle}
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
        style={{ ...scrollStyle, height }}
      >
        {filtered.length === 0 ? (
          <p className="event-log__empty" style={emptyStyle}>
            No events to show.
          </p>
        ) : (
          <ul style={listStyle}>
            {padTop > 0 ? (
              <li aria-hidden="true" style={{ height: padTop }} />
            ) : null}
            {visible.map((e) => (
              <li
                key={`${e.ts}-${e.kind}-${e.node ?? ""}-${e.title}`}
                className={`event-log__row event-log__row--${e.severity}`}
                style={rowStyle(e.severity)}
              >
                <span
                  className={`event-log__chip event-log__chip--${e.severity}`}
                  style={chipStyle(e.severity)}
                >
                  {e.severity}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="event-log__title" style={titleStyle}>
                    {e.title}
                    {e.node ? (
                      <span className="event-log__node" style={nodeTagStyle}>
                        {e.node}
                      </span>
                    ) : null}
                  </div>
                  {e.detail ? (
                    <div className="event-log__detail" style={detailStyle}>
                      {e.detail}
                    </div>
                  ) : null}
                </div>
                <time
                  className="event-log__ts"
                  dateTime={new Date(e.ts).toISOString()}
                  style={tsStyle}
                >
                  {formatTs(e.ts)}
                </time>
              </li>
            ))}
            {padBottom > 0 ? (
              <li aria-hidden="true" style={{ height: padBottom }} />
            ) : null}
          </ul>
        )}
      </div>
      {/* Spacer asserts the virtual total height for scrollbar fidelity. */}
      {useVirtual ? (
        <div aria-hidden="true" style={{ height: 0, overflow: "hidden" }}>
          {totalHeight}
        </div>
      ) : null}
    </div>
  );
}

const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  marginBottom: 8,
  flexWrap: "wrap",
};

function filterBtnStyle(active: boolean): CSSProperties {
  return {
    background: active ? "var(--accent-control-bg, #0078d4)" : "transparent",
    border: "1px solid var(--border)",
    borderRadius: 6,
    color: active ? "var(--accent-control-fg, #ffffff)" : "var(--text-secondary)",
    cursor: "pointer",
    fontSize: 12,
    fontWeight: 600,
    minHeight: 28,
    padding: "3px 10px",
  };
}

const scrollStyle: CSSProperties = {
  overflowY: "auto",
  border: "1px solid var(--border)",
  borderRadius: 8,
  background: "var(--bg-secondary)",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
};

function rowStyle(severity: EventSeverity): CSSProperties {
  return {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    height: ROW_HEIGHT,
    boxSizing: "border-box",
    padding: "8px 12px",
    borderBottom: "1px solid var(--border)",
    borderLeft: `3px solid ${SEVERITY_COLOR[severity]}`,
  };
}

function chipStyle(severity: EventSeverity): CSSProperties {
  return {
    flexShrink: 0,
    alignSelf: "center",
    background: SEVERITY_COLOR[severity],
    color: "var(--text-on-accent, #fff)",
    borderRadius: 9999,
    fontSize: 10,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    padding: "2px 8px",
    minWidth: 56,
    textAlign: "center",
  };
}

const titleStyle: CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: "var(--text-primary)",
  display: "flex",
  alignItems: "center",
  gap: 6,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const nodeTagStyle: CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  color: "var(--text-secondary)",
  background: "var(--bg-tertiary)",
  borderRadius: 4,
  padding: "1px 6px",
};

const detailStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--text-secondary)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const tsStyle: CSSProperties = {
  flexShrink: 0,
  alignSelf: "center",
  fontSize: 11,
  color: "var(--text-secondary)",
  whiteSpace: "nowrap",
};

const emptyStyle: CSSProperties = {
  color: "var(--text-secondary)",
  fontSize: 13,
  margin: 16,
  textAlign: "center",
};

const srOnlyStyle: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};
