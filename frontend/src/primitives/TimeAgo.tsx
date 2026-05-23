/**
 * <TimeAgo /> — accessible relative-time primitive.
 *
 * Addresses Runner_Dashboard#725 (D6). Renders a semantic `<time>`
 * element so screen readers announce e.g. "2 minutes ago" (via the
 * formatted text) while the raw ISO value is preserved in the
 * `dateTime` attribute and tooltip for power users.
 *
 * Engineering principles:
 * - DRY: delegates to `useTimeAgo`; never inlines formatting logic.
 * - Orthogonality: a render failure here cannot cascade — invalid input
 *   degrades to the raw string, never throws.
 */
import { type HTMLAttributes } from 'react';
import { useTimeAgo, type UseTimeAgoOptions } from '../hooks/useTimeAgo';

export interface TimeAgoProps
  extends Omit<HTMLAttributes<HTMLTimeElement>, 'title' | 'children'> {
  /** ISO-8601 string or Date instance to render. */
  iso: string | Date;
  /**
   * Tooltip override. Defaults to the raw ISO string so hovering reveals
   * the precise timestamp.
   */
  title?: string;
  /** Forwarded to `useTimeAgo`. */
  live?: UseTimeAgoOptions['live'];
}

function toIsoString(value: string | Date): string {
  if (value instanceof Date) {
    return Number.isFinite(value.getTime()) ? value.toISOString() : '';
  }
  return value;
}

export function TimeAgo({ iso, title, live, ...rest }: TimeAgoProps) {
  const formatted = useTimeAgo(iso, { live });
  const isoString = toIsoString(iso);
  return (
    <time dateTime={isoString} title={title ?? isoString} {...rest}>
      {formatted}
    </time>
  );
}
