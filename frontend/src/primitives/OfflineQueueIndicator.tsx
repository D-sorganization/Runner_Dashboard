/**
 * OfflineQueueIndicator — small status badge shown when there are mutations
 * queued in IndexedDB awaiting replay (issue #380).
 *
 * Usage:
 *   <OfflineQueueIndicator queuedCount={n} isOnline={navigator.onLine} />
 *
 * When queuedCount === 0 and isOnline, renders nothing (null).
 */

import type { CSSProperties } from "react"

export interface OfflineQueueIndicatorProps {
  queuedCount: number
  isOnline: boolean
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
}

const queuedStyle: CSSProperties = {
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
}

export function OfflineQueueIndicator({
  queuedCount,
  isOnline,
}: OfflineQueueIndicatorProps) {
  if (isOnline && queuedCount === 0) return null

  if (!isOnline) {
    return (
      <span style={offlineStyle} role="status" aria-live="polite">
        <span aria-hidden="true">⚡</span>
        Offline
        {queuedCount > 0 && (
          <span>
            {" "}
            — {queuedCount} action{queuedCount === 1 ? "" : "s"} queued
          </span>
        )}
      </span>
    )
  }

  // Online but queue not yet drained
  return (
    <span style={queuedStyle} role="status" aria-live="polite">
      <span aria-hidden="true">↻</span>
      Replaying {queuedCount} queued action{queuedCount === 1 ? "" : "s"}…
    </span>
  )
}
