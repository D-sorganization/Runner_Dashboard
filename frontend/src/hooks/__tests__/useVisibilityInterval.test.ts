// @vitest-environment jsdom
/**
 * Tests for useVisibilityInterval (#3750).
 *
 * Covers:
 * 1. fires on the given interval while the tab is visible
 * 2. stops firing while document.hidden is true
 * 3. fires immediately on visibilitychange -> visible, then resumes the interval
 * 4. clears the timer on unmount
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useVisibilityInterval } from '../useVisibilityInterval';

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.useFakeTimers();
  setHidden(false);
});

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
});

describe('useVisibilityInterval', () => {
  it('fires on the given interval while visible', () => {
    const cb = vi.fn();
    renderHook(() => useVisibilityInterval(cb, 1000));

    vi.advanceTimersByTime(3000);

    expect(cb).toHaveBeenCalledTimes(3);
  });

  it('stops firing while document.hidden is true', () => {
    const cb = vi.fn();
    renderHook(() => useVisibilityInterval(cb, 1000));

    vi.advanceTimersByTime(2000);
    expect(cb).toHaveBeenCalledTimes(2);

    setHidden(true);
    vi.advanceTimersByTime(5000);

    expect(cb).toHaveBeenCalledTimes(2);
  });

  it('fires immediately on visibilitychange -> visible, then resumes the interval', () => {
    const cb = vi.fn();
    renderHook(() => useVisibilityInterval(cb, 1000));

    setHidden(true);
    vi.advanceTimersByTime(5000);
    expect(cb).toHaveBeenCalledTimes(0);

    setHidden(false);
    expect(cb).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(2000);
    expect(cb).toHaveBeenCalledTimes(3);
  });

  it('clears the timer on unmount', () => {
    const cb = vi.fn();
    const { unmount } = renderHook(() => useVisibilityInterval(cb, 1000));

    vi.advanceTimersByTime(1000);
    expect(cb).toHaveBeenCalledTimes(1);

    unmount();
    vi.advanceTimersByTime(5000);

    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('always uses the latest callback without resetting the timer', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ cb }) => useVisibilityInterval(cb, 1000), {
      initialProps: { cb: first },
    });

    vi.advanceTimersByTime(1000);
    expect(first).toHaveBeenCalledTimes(1);

    rerender({ cb: second });
    vi.advanceTimersByTime(1000);

    expect(second).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
  });
});
