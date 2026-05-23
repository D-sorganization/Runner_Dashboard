/**
 * Tests for useTimeAgo hook — relative timestamp utility.
 *
 * Covers issue #725 (D6).
 *
 * Engineering principles:
 * - TDD: this file was authored before the implementation.
 * - DbC: precondition (parseable input) and postcondition (non-empty,
 *   human-readable output) are asserted via parameterised cases.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTimeAgo, formatTimeAgo } from '../useTimeAgo';

const NOW = new Date('2026-05-22T12:00:00Z').getTime();

describe('formatTimeAgo (pure)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    ['0 seconds ago renders "just now"', 0, 'just now'],
    ['30 seconds ago renders "just now"', 30_000, 'just now'],
    ['90 seconds ago renders "1m ago"', 90_000, '1m ago'],
    ['2 hours ago renders "2h ago"', 2 * 60 * 60 * 1000, '2h ago'],
    ['26 hours ago renders "yesterday"', 26 * 60 * 60 * 1000, 'yesterday'],
    ['8 days ago renders an absolute date (e.g. May 14)', 8 * 24 * 60 * 60 * 1000, /^[A-Z][a-z]{2} \d{1,2}$/],
    ['400 days ago renders an absolute date with year', 400 * 24 * 60 * 60 * 1000, /\d{4}/],
  ])('%s', (_label, deltaMs, expected) => {
    const past = new Date(NOW - deltaMs).toISOString();
    const out = formatTimeAgo(past);
    if (expected instanceof RegExp) {
      expect(out).toMatch(expected);
    } else {
      expect(out).toBe(expected);
    }
    // DbC postcondition: non-empty string.
    expect(out.length).toBeGreaterThan(0);
  });

  it('future timestamps render "soon"', () => {
    const future = new Date(NOW + 60_000).toISOString();
    expect(formatTimeAgo(future)).toBe('soon');
  });

  it('invalid input returns the raw string and warns', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(formatTimeAgo('not-a-date')).toBe('not-a-date');
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it('accepts a Date object as input', () => {
    expect(formatTimeAgo(new Date(NOW - 5 * 60 * 1000))).toBe('5m ago');
  });
});

describe('useTimeAgo (hook)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns the same string formatTimeAgo would produce', () => {
    const iso = new Date(NOW - 60_000).toISOString();
    const { result } = renderHook(() => useTimeAgo(iso));
    expect(result.current).toBe('1m ago');
  });

  it('re-renders on its own clock when live=true (default)', () => {
    const iso = new Date(NOW - 30_000).toISOString();
    const { result } = renderHook(() => useTimeAgo(iso));
    expect(result.current).toBe('just now');
    // Advance the fake-timer queue — vitest's advanceTimersByTime moves the
    // mocked Date.now() forward in lockstep, so the 30s interval fires with
    // a fresh "now". Total elapsed: 30s initial + 90s advance = 2m.
    act(() => {
      vi.advanceTimersByTime(90_000);
    });
    expect(result.current).toBe('2m ago');
  });

  it('does not start a timer when live=false', () => {
    const iso = new Date(NOW - 30_000).toISOString();
    const setIntervalSpy = vi.spyOn(window, 'setInterval');
    renderHook(() => useTimeAgo(iso, { live: false }));
    expect(setIntervalSpy).not.toHaveBeenCalled();
    setIntervalSpy.mockRestore();
  });
});
