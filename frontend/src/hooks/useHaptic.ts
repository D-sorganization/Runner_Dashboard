import { useCallback } from 'react';

/**
 * Haptic feedback hook for mobile devices.
 * Maps to navigator.vibrate() with predefined patterns.
 * Respects prefers-reduced-motion: reduce.
 */
export function useHaptic() {
  const canVibrate = typeof navigator !== 'undefined' && 'vibrate' in navigator;

  const prefersReducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const vibrate = useCallback(
    (pattern: number | number[]) => {
      if (!canVibrate || prefersReducedMotion) return;
      try {
        navigator.vibrate(pattern);
      } catch {
        // Silently ignore vibration errors
      }
    },
    [canVibrate, prefersReducedMotion]
  );

  const light = useCallback(() => {
    vibrate(10);
  }, [vibrate]);

  const medium = useCallback(() => {
    vibrate(20);
  }, [vibrate]);

  const heavy = useCallback(() => {
    vibrate([0, 30, 50, 30]);
  }, [vibrate]);

  const success = useCallback(() => {
    vibrate([0, 20, 50, 20]);
  }, [vibrate]);

  const error = useCallback(() => {
    vibrate([0, 50, 30, 50, 30, 50]);
  }, [vibrate]);

  return { light, medium, heavy, success, error };
}

export type HapticFeedback = ReturnType<typeof useHaptic>;