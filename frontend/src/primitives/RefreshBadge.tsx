/**
 * RefreshBadge — displays data freshness state with live/stale/error variants (D3 / issue #722).
 *
 * Preconditions:
 *  - staleness must be a Staleness object from useStalenessWarning.
 *  - onRetry must be a callable function.
 *
 * Postconditions:
 *  - fresh  → green dot + "Live now"
 *  - stale  → neutral + "Updated X ago" (uses TimeAgo)
 *  - error  → red + "Network error · Retry" with clickable Retry button
 *  - The container has role="status" and aria-live="polite".
 */

import React from "react";
import type { Staleness } from "../hooks/useStalenessWarning";
import { TimeAgo } from "./TimeAgo";

export interface RefreshBadgeProps {
  staleness: Staleness;
  onRetry: () => void;
}

const dotStyle: React.CSSProperties = {
  display: "inline-block",
  width: "8px",
  height: "8px",
  borderRadius: "50%",
  marginRight: "6px",
  flexShrink: 0,
};

const containerStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  fontSize: "0.8rem",
  fontWeight: 500,
};

export function RefreshBadge({ staleness, onRetry }: RefreshBadgeProps) {
  const { state, lastSuccessAt } = staleness;

  if (state === "fresh") {
    return (
      <span
        role="status"
        aria-live="polite"
        style={{
          ...containerStyle,
          color: "var(--status-healthy-fg, #3fb950)",
        }}
      >
        <span
          style={{
            ...dotStyle,
            background: "var(--status-healthy-fg, #3fb950)",
          }}
          aria-hidden="true"
        />
        Live now
      </span>
    );
  }

  if (state === "stale") {
    return (
      <span
        role="status"
        aria-live="polite"
        style={{ ...containerStyle, color: "var(--text-secondary, #8b949e)" }}
      >
        <span
          style={{ ...dotStyle, background: "var(--text-muted, #8b949e)" }}
          aria-hidden="true"
        />
        {lastSuccessAt ? (
          <>
            Updated <TimeAgo iso={lastSuccessAt} live={false} />
          </>
        ) : (
          "Not yet loaded"
        )}
      </span>
    );
  }

  // state === 'error'
  return (
    <span
      role="status"
      aria-live="polite"
      style={{ ...containerStyle, color: "var(--status-critical-fg, #f85149)" }}
    >
      <span
        style={{
          ...dotStyle,
          background: "var(--status-critical-fg, #f85149)",
        }}
        aria-hidden="true"
      />
      Network error{" · "}
      <button
        onClick={onRetry}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: "var(--accent-blue, #58a6ff)",
          fontSize: "inherit",
          fontWeight: "inherit",
          textDecoration: "underline",
          marginLeft: "2px",
        }}
      >
        Retry
      </button>
    </span>
  );
}
