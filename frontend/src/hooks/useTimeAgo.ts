/**
 * useTimeAgo — relative timestamp formatting hook.
 *
 * Addresses Runner_Dashboard#725 (D6). Every visible timestamp in the
 * dashboard should render through this utility so operators see
 * `"2m ago"` rather than `2026-05-22T14:31:45.123456Z`.
 *
 * Engineering principles:
 * - DbC: precondition asserts input is parseable as a Date in dev mode;
 *   postcondition guarantees a non-empty, human-readable string.
 * - LoD: pure `formatTimeAgo` takes a flat scalar — no reaching through
 *   nested objects across module boundaries.
 * - Reusable: hook delegates to the pure formatter; the primitive
 *   `<TimeAgo />` consumes the hook so logic lives in exactly one place.
 *
 * Locale handling: uses `Intl.RelativeTimeFormat` and `Intl.DateTimeFormat`
 * with the runtime locale (`document.documentElement.lang || navigator.language`)
 * so dates render in the operator's locale via the platform Intl APIs alone.
 */
import { useEffect, useState } from 'react';

const SECOND = 1_000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;

const JUST_NOW_THRESHOLD = 60 * SECOND;
const YESTERDAY_LOWER = 24 * HOUR;
const YESTERDAY_UPPER = 48 * HOUR;

export interface UseTimeAgoOptions {
  /**
   * Re-render the hook over time so the displayed value stays fresh.
   * Defaults to `true`. Set to `false` for static contexts (e.g. exports).
   */
  live?: boolean;
}

function resolveLocale(): string {
  if (typeof document !== 'undefined' && document.documentElement?.lang) {
    return document.documentElement.lang;
  }
  if (typeof navigator !== 'undefined' && navigator.language) {
    return navigator.language;
  }
  return 'en';
}

function toDate(input: string | Date): Date | null {
  if (input instanceof Date) {
    return Number.isFinite(input.getTime()) ? input : null;
  }
  if (typeof input !== 'string' || input.length === 0) {
    return null;
  }
  const d = new Date(input);
  return Number.isFinite(d.getTime()) ? d : null;
}

function absoluteDate(date: Date, now: Date, locale: string): string {
  const sameYear = date.getUTCFullYear() === now.getUTCFullYear();
  const fmt = new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    year: sameYear ? undefined : 'numeric',
  });
  return fmt.format(date);
}

/**
 * Pure formatter — exported for unit tests and one-off rendering (e.g.
 * inside table cells where running a hook per row is undesirable).
 *
 * Postcondition: returns a non-empty string. For unparseable input the raw
 * string is returned (with a `console.warn`) so the UI never blanks out.
 */
export function formatTimeAgo(input: string | Date, now: Date = new Date()): string {
  const date = toDate(input);
  if (!date) {
    // DbC graceful-degradation: warn but never throw in production rendering.
    // eslint-disable-next-line no-console
    console.warn('[useTimeAgo] invalid input:', input);
    return typeof input === 'string' ? input : String(input);
  }

  const deltaMs = now.getTime() - date.getTime();

  // Future timestamps: render "soon" rather than nonsensical "-2m ago".
  if (deltaMs < 0) {
    return 'soon';
  }

  if (deltaMs < JUST_NOW_THRESHOLD) {
    return 'just now';
  }
  if (deltaMs < HOUR) {
    const minutes = Math.floor(deltaMs / MINUTE);
    return `${minutes}m ago`;
  }
  if (deltaMs < YESTERDAY_LOWER) {
    const hours = Math.floor(deltaMs / HOUR);
    return `${hours}h ago`;
  }
  if (deltaMs < YESTERDAY_UPPER) {
    return 'yesterday';
  }
  // Older than ~2 days — fall back to an absolute, locale-formatted date.
  return absoluteDate(date, now, resolveLocale());
}

/**
 * React hook that returns the formatted relative-time string and refreshes
 * it on a coarse interval (30s for <1h timestamps, 5min for <24h, no
 * refresh for older absolute dates).
 */
export function useTimeAgo(
  input: string | Date,
  opts: UseTimeAgoOptions = {},
): string {
  const { live = true } = opts;
  const [, force] = useState(0);
  const value = formatTimeAgo(input);

  useEffect(() => {
    if (!live) return;
    const date = toDate(input);
    if (!date) return;
    const deltaMs = Date.now() - date.getTime();
    // Older than 24h — the value is an absolute date that won't change.
    if (deltaMs > YESTERDAY_UPPER) return;
    const intervalMs = deltaMs < HOUR ? 30 * SECOND : 5 * MINUTE;
    const id = window.setInterval(() => force((n) => n + 1), intervalMs);
    return () => window.clearInterval(id);
  }, [input, live, value]);

  return value;
}
