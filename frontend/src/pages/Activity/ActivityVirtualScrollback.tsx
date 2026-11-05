import React, { useEffect, useRef, useState } from "react";
import type { ActivityEnvelope } from "./activityApi";
import { isGapEvent } from "./activityApi";

const ROW_HEIGHT = 52;
const OVERSCAN = 4;
const VIRTUALIZE_THRESHOLD = 30;
const DEFAULT_HEIGHT = 420;

export type ActivityScrollbackRow = {
  event: ActivityEnvelope;
  conflictLabels: string[];
};

export function ActivityVirtualScrollback({
  rows,
  height = DEFAULT_HEIGHT,
}: {
  rows: ActivityScrollbackRow[];
  height?: number;
}): React.ReactElement {
  const containerRef = useRef<HTMLUListElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(height);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.style.maxHeight = `${height}px`;
    setViewport(el.clientHeight || height);
  }, [height]);

  const useVirtual = rows.length > VIRTUALIZE_THRESHOLD;
  const totalHeight = rows.length * ROW_HEIGHT;
  let startIdx = 0;
  let endIdx = rows.length;
  if (useVirtual) {
    startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visibleCount = Math.ceil(viewport / ROW_HEIGHT) + OVERSCAN * 2;
    endIdx = Math.min(rows.length, startIdx + visibleCount);
  }
  const visible = rows.slice(startIdx, endIdx);
  const topPad = useVirtual ? startIdx * ROW_HEIGHT : 0;
  const bottomPad = useVirtual ? Math.max(0, totalHeight - endIdx * ROW_HEIGHT) : 0;

  return (
    <ul
      role="list"
      className="activity-page__scrollback"
      ref={containerRef}
      data-testid="activity-scrollback"
      data-virtualized={useVirtual ? "true" : "false"}
      onScroll={(e) => setScrollTop((e.target as HTMLElement).scrollTop)}
      style={{ overflowY: "auto" }}
    >
      {useVirtual && topPad > 0 ? (
        <li aria-hidden="true" className="activity-page__scroll-pad" style={{ height: topPad }} />
      ) : null}
      {visible.map(({ event, conflictLabels }) => (
        <li
          key={event.event_id}
          className={isGapEvent(event) ? "activity-page__gap" : undefined}
          style={useVirtual ? { minHeight: ROW_HEIGHT } : undefined}
        >
          <strong>{event.kind}</strong> <time dateTime={event.ts}>{event.ts}</time>
          {event.repo ? ` — ${event.repo}` : ""}
          {conflictLabels.map((label) => (
            <span key={label} className="activity-page__conflict-badge" role="status">
              {" "}
              [{label}]
            </span>
          ))}
          {isGapEvent(event) && <span role="status"> — gap</span>}
        </li>
      ))}
      {useVirtual && bottomPad > 0 ? (
        <li aria-hidden="true" className="activity-page__scroll-pad" style={{ height: bottomPad }} />
      ) : null}
    </ul>
  );
}
