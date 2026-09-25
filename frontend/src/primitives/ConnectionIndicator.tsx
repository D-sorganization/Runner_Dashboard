/**
 * ConnectionIndicator.tsx — Global connection indicator (issue #1304, epic #1347).
 *
 * Fed by browser online/offline events, fetch failures (TanStack Query),
 * SSE connection state, and the IndexedDB mutation queue.
 *
 * Visual states:
 * - Online (nominal): renders null (keeps topbar clean).
 * - Reconnecting: amber badge with spinner/icon ("Reconnecting…").
 * - Offline: red badge ("Offline — N actions queued").
 * - Replaying: amber badge ("Replaying N queued actions…").
 */
import type { CSSProperties, ReactElement } from "react";
import { useMutationQueue } from "../hooks/useMutationQueue";

export interface ConnectionIndicatorProps {
  /** Optional override for isOnline. Defaults to ambient mutation queue / navigator state. */
  isOnline?: boolean;
  /** Optional override for queuedCount. Defaults to ambient mutation queue count. */
  queuedCount?: number;
  /** True when SSE or network queries are actively attempting reconnection. */
  isReconnecting?: boolean;
}

const offlineStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 10px",
  borderRadius: 12,
  fontSize: 12,
  fontWeight: 600,
  background: "rgba(248,81,73,0.15)",
  color: "var(--accent-red, #f85149)",
  border: "1px solid var(--accent-red, #f85149)",
};

const warningStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 10px",
  borderRadius: 12,
  fontSize: 12,
  fontWeight: 600,
  background: "rgba(210,153,34,0.15)",
  color: "var(--accent-yellow, #d2992a)",
  border: "1px solid var(--accent-yellow, #d2992a)",
};

export function ConnectionIndicator({
  isOnline: propOnline,
  queuedCount: propQueuedCount,
  isReconnecting = false,
}: ConnectionIndicatorProps): ReactElement | null {
  // Read ambient mutation queue state if props not explicitly supplied
  const queue = useMutationQueue();
  const online = propOnline !== undefined ? propOnline : queue.isOnline;
  const count = propQueuedCount !== undefined ? propQueuedCount : queue.queuedCount;

  if (online && count === 0 && !isReconnecting) {
    return null;
  }

  if (!online) {
    return (
      <span style={offlineStyle} role="status" aria-live="polite" data-testid="connection-offline">
        <span aria-hidden="true">⚡</span>
        Offline
        {count > 0 && (
          <span>
            {" "}
            — {count} action{count === 1 ? "" : "s"} queued
          </span>
        )}
      </span>
    );
  }

  if (isReconnecting) {
    return (
      <span style={warningStyle} role="status" aria-live="polite" data-testid="connection-reconnecting">
        <span aria-hidden="true">↻</span>
        Reconnecting…
      </span>
    );
  }

  // Online but queued items being replayed
  return (
    <span style={warningStyle} role="status" aria-live="polite" data-testid="connection-replaying">
      <span aria-hidden="true">↻</span>
      Replaying {count} queued action{count === 1 ? "" : "s"}…
    </span>
  );
}

export default ConnectionIndicator;
