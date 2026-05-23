// @vitest-environment jsdom
/**
 * Tests for useTimeAgo hook (D6 / issue #725).
 *
 * Covers:
 * 1. 0s → "just now"
 * 2. 30s ago → "30s ago"
 * 3. 90s ago → "1m ago"
 * 4. 2h ago → "2h ago"
 * 5. 26h ago → "yesterday"
 * 6. 8 days ago → relative date (e.g. "May 15")
 * 7. 400 days ago → date with year (e.g. "Jan 18, 2025")
 * 8. Future date → "soon"
 * 9. Invalid input → returns raw string
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useTimeAgo } from '../useTimeAgo';

// Fixed reference: 2026-05-23T12:00:00Z
const NOW = new Date('2026-05-23T12:00:00Z').getTime();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

function sec(n: number) { return new Date(NOW - n * 1000).toISOString(); }
function min(n: number) { return new Date(NOW - n * 60 * 1000).toISOString(); }
function hours(n: number) { return new Date(NOW - n * 60 * 60 * 1000).toISOString(); }
function days(n: number) { return new Date(NOW - n * 24 * 60 * 60 * 1000).toISOString(); }

describe('useTimeAgo', () => {
  it('returns "just now" for < 30s ago', () => {
    const { result } = renderHook(() => useTimeAgo(sec(5)));
    expect(result.current).toBe('just now');
  });

  it('returns "Xs ago" for seconds in [30, 59]', () => {
    const { result } = renderHook(() => useTimeAgo(sec(30)));
    expect(result.current).toMatch(/^\d+s ago$/);
  });

  it('returns "1m ago" for ~90s', () => {
    const { result } = renderHook(() => useTimeAgo(sec(90)));
    expect(result.current).toBe('1m ago');
  });

  it('returns "Xm ago" for minutes under 60', () => {
    const { result } = renderHook(() => useTimeAgo(min(45)));
    expect(result.current).toMatch(/^\d+m ago$/);
  });

  it('returns "2h ago" for 2 hours', () => {
    const { result } = renderHook(() => useTimeAgo(hours(2)));
    expect(result.current).toBe('2h ago');
  });

  it('returns "Xh ago" for hours under 24', () => {
    const { result } = renderHook(() => useTimeAgo(hours(10)));
    expect(result.current).toMatch(/^\d+h ago$/);
  });

  it('returns "yesterday" for ~26 hours ago', () => {
    const { result } = renderHook(() => useTimeAgo(hours(26)));
    expect(result.current).toBe('yesterday');
  });

  it('returns date string for 8 days ago (no year)', () => {
    const { result } = renderHook(() => useTimeAgo(days(8)));
    // Should be a date like "May 15" — no year since same year
    expect(result.current).toMatch(/[A-Z][a-z]+ \d+/);
    expect(result.current).not.toMatch(/\d{4}/);
  });

  it('returns date with year for 400 days ago', () => {
    const { result } = renderHook(() => useTimeAgo(days(400)));
    // Should include a 4-digit year since it crosses year boundary
    expect(result.current).toMatch(/\d{4}/);
  });

  it('returns "soon" for a future date', () => {
    const future = new Date(NOW + 5 * 60 * 1000).toISOString();
    const { result } = renderHook(() => useTimeAgo(future));
    expect(result.current).toBe('soon');
  });

  it('returns raw string for invalid input and warns', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useTimeAgo('not-a-date'));
    expect(result.current).toBe('not-a-date');
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it('accepts a Date object', () => {
    const { result } = renderHook(() => useTimeAgo(new Date(NOW - 5000)));
    expect(result.current).toBe('just now');
  });
});
