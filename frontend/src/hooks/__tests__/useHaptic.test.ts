import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useHaptic } from '../useHaptic';

describe('useHaptic', () => {
  const originalVibrate = navigator.vibrate;
  let vibrateMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vibrateMock = vi.fn();
    Object.defineProperty(navigator, 'vibrate', {
      value: vibrateMock,
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(navigator, 'vibrate', {
      value: originalVibrate,
      writable: true,
      configurable: true,
    });
  });

  it('should call navigator.vibrate with light pattern', () => {
    const { result } = renderHook(() => useHaptic());
    result.current.light();
    expect(vibrateMock).toHaveBeenCalledWith(10);
  });

  it('should call navigator.vibrate with medium pattern', () => {
    const { result } = renderHook(() => useHaptic());
    result.current.medium();
    expect(vibrateMock).toHaveBeenCalledWith(20);
  });

  it('should call navigator.vibrate with heavy pattern', () => {
    const { result } = renderHook(() => useHaptic());
    result.current.heavy();
    expect(vibrateMock).toHaveBeenCalledWith([0, 30, 50, 30]);
  });

  it('should call navigator.vibrate with success pattern', () => {
    const { result } = renderHook(() => useHaptic());
    result.current.success();
    expect(vibrateMock).toHaveBeenCalledWith([0, 20, 50, 20]);
  });

  it('should call navigator.vibrate with error pattern', () => {
    const { result } = renderHook(() => useHaptic());
    result.current.error();
    expect(vibrateMock).toHaveBeenCalledWith([0, 50, 30, 50, 30, 50]);
  });

  it('should not vibrate when prefers-reduced-motion is reduce', () => {
    Object.defineProperty(window, 'matchMedia', {
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query === '(prefers-reduced-motion: reduce)',
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
      writable: true,
    });

    const { result } = renderHook(() => useHaptic());
    result.current.light();
    expect(vibrateMock).not.toHaveBeenCalled();
  });

  it('should not vibrate when navigator.vibrate is unavailable', () => {
    Object.defineProperty(navigator, 'vibrate', {
      value: undefined,
      writable: true,
      configurable: true,
    });

    const { result } = renderHook(() => useHaptic());
    result.current.light();
    expect(vibrateMock).not.toHaveBeenCalled();
  });
});