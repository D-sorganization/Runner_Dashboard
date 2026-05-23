/**
 * useTimeAgo — human-readable relative time hook (D6 / issue #725).
 *
 * Preconditions:
 *  - input must be a valid ISO-8601 string, Date object, or numeric timestamp.
 *
 * Postconditions:
 *  - Returns a human-readable string like "just now", "3m ago", "yesterday", "May 15", "Jan 18, 2025".
 *  - For future dates returns "soon".
 *  - For invalid input returns the raw string and emits console.warn.
 *  - When live=true (default), the component re-renders periodically.
 */

import { useState, useEffect } from 'react';

export interface UseTimeAgoOptions {
  /** Auto-refresh the string over time. Defaults to true. */
  live?: boolean;
  /** Milliseconds under which "just now" is shown. Defaults to 30_000. */
  threshold?: number;
}

function formatRelative(input: string | Date): string {
  const date = input instanceof Date ? input : new Date(input as string);

  if (isNaN(date.getTime())) {
    console.warn('[useTimeAgo] Invalid date input:', input);
    return String(input);
  }

  const now = Date.now();
  const diffMs = now - date.getTime();

  // Future
  if (diffMs < 0) return 'soon';

  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 30) return 'just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  if (diffHour < 48) return 'yesterday';

  // More than 2 days — show a calendar date
  const nowYear = new Date(now).getFullYear();
  const dateYear = date.getFullYear();

  if (nowYear === dateYear) {
    // Same year: "May 15"
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
  }
  // Different year: "Jan 18, 2025"
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

function getRefreshInterval(input: string | Date): number | null {
  const date = input instanceof Date ? input : new Date(input as string);
  if (isNaN(date.getTime())) return null;

  const diffMs = Math.abs(Date.now() - date.getTime());
  const diffMin = diffMs / 60_000;

  if (diffMin < 60) return 30_000;     // Refresh every 30s when < 1 hour
  if (diffMin < 1440) return 300_000;  // Refresh every 5m when < 24h
  return null;                         // No live refresh for older dates
}

export function useTimeAgo(
  input: string | Date,
  opts: UseTimeAgoOptions = {},
): string {
  const { live = true } = opts;

  const [value, setValue] = useState<string>(() => formatRelative(input));

  useEffect(() => {
    // Recalculate when input changes
    setValue(formatRelative(input));
  }, [input]);

  useEffect(() => {
    if (!live) return;

    const interval = getRefreshInterval(input);
    if (interval == null) return;

    const id = setInterval(() => {
      setValue(formatRelative(input));
    }, interval);

    return () => clearInterval(id);
  }, [input, live]);

  return value;
}
