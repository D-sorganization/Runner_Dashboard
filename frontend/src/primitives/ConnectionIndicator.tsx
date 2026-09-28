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

function OfflineIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <line x1="1" y1="1" x2="23" y2="23" />
      <path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55" />
      <path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39" />
      <path d="M10.71 5.05A16 16 0 0 1 22.58 9" />
      <path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88" />
      <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
      <line x1="12" y1="20" x2="12.01" y2="20" />
    </svg>
  );
}

function SyncIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

const offlineStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 8px",
  borderRadius: "var(--radius-pill, 9999px)",
  fontSize: 11.5,
  fontWeight: 600,
  background: "var(--badge-danger-bg)",
  color: "var(--accent-red)",
  border: "1px solid var(--accent-red)",
};

const warningStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 8px",
  borderRadius: "var(--radius-pill, 9999px)",
  fontSize: 11.5,
  fontWeight: 600,
  background: "var(--badge-warning-bg)",
  color: "var(--accent-yellow)",
  border: "1px solid var(--accent-yellow)",
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
        <OfflineIcon />
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
        <SyncIcon />
        Reconnecting…
      </span>
    );
  }

  // Online but queued items being replayed
  return (
    <span style={warningStyle} role="status" aria-live="polite" data-testid="connection-replaying">
      <SyncIcon />
      Replaying {count} queued action{count === 1 ? "" : "s"}…
    </span>
  );
}

export default ConnectionIndicator;
