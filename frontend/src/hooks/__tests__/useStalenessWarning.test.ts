// @vitest-environment jsdom
/**
 * Tests for useStalenessWarning hook (D3 / issue #722).
 *
 * Covers:
 * 1. fresh: dataUpdatedAt=now, failureCount=0 → state="fresh"
 * 2. stale: dataUpdatedAt=now-90s, failureCount=0 → state="stale"
 * 3. error: failureCount>=2 → state="error" regardless of lastSuccess
 * 4. isFetching is forwarded
 * 5. Custom freshMs threshold is honoured
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useStalenessWarning } from '../useStalenessWarning';

const NOW = Date.now();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

const makeQuery = (overrides: Partial<{
  dataUpdatedAt: number;
  errorUpdatedAt: number;
  failureCount: number;
  isFetching: boolean;
}> = {}) => ({
  dataUpdatedAt: NOW,
  errorUpdatedAt: 0,
  failureCount: 0,
  isFetching: false,
  ...overrides,
});

describe('useStalenessWarning', () => {
  it('returns "fresh" when data is recent and no failures', () => {
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ dataUpdatedAt: NOW })),
    );
    expect(result.current.state).toBe('fresh');
  });

  it('returns "stale" when data is older than freshMs', () => {
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ dataUpdatedAt: NOW - 90_000 }), 60_000),
    );
    expect(result.current.state).toBe('stale');
  });

  it('returns "error" when failureCount >= 2', () => {
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ failureCount: 2, dataUpdatedAt: NOW })),
    );
    expect(result.current.state).toBe('error');
  });

  it('returns "error" for failureCount >= 2 even if data is fresh', () => {
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ failureCount: 3, dataUpdatedAt: NOW })),
    );
    expect(result.current.state).toBe('error');
  });

  it('returns lastSuccessAt when dataUpdatedAt > 0', () => {
    const ts = NOW - 5000;
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ dataUpdatedAt: ts })),
    );
    expect(result.current.lastSuccessAt).toEqual(new Date(ts));
  });

  it('returns lastSuccessAt=null when dataUpdatedAt=0', () => {
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ dataUpdatedAt: 0 })),
    );
    expect(result.current.lastSuccessAt).toBeNull();
  });

  it('forwards isFetching', () => {
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ isFetching: true })),
    );
    expect(result.current.isFetching).toBe(true);
  });

  it('respects custom freshMs threshold', () => {
    // 30s ago with freshMs=10_000 (10s) → stale
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ dataUpdatedAt: NOW - 30_000 }), 10_000),
    );
    expect(result.current.state).toBe('stale');
  });

  it('returns "fresh" with custom freshMs when data is within threshold', () => {
    // 5s ago with freshMs=10_000 → fresh
    const { result } = renderHook(() =>
      useStalenessWarning(makeQuery({ dataUpdatedAt: NOW - 5_000 }), 10_000),
    );
    expect(result.current.state).toBe('fresh');
  });
});
