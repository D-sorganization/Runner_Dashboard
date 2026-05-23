/**
 * TimeAgo — renders a <time> element with human-readable relative time (D6 / issue #725).
 *
 * Preconditions:
 *  - date must be a valid ISO-8601 string or Date object.
 *
 * Postconditions:
 *  - Renders a <time> element with dateTime and title attributes set to the full ISO string.
 *  - The visible text is the output of useTimeAgo (e.g. "3m ago", "yesterday").
 */

import React from 'react';
import { useTimeAgo } from '../hooks/useTimeAgo';

export interface TimeAgoProps {
  /** ISO-8601 string or Date object. */
  date: string | Date;
  /** Auto-refresh the displayed text. Defaults to true. */
  live?: boolean;
  className?: string;
}

export function TimeAgo({ date, live = true, className }: TimeAgoProps) {
  const relative = useTimeAgo(date, { live });
  const fullIso = date instanceof Date ? date.toISOString() : date;

  return (
    <time dateTime={fullIso} title={fullIso} className={className}>
      {relative}
    </time>
  );
}
