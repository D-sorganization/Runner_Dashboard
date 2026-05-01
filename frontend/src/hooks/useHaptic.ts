/**
 * useHaptic — vibration feedback hook for high-stakes confirmations (#420).
 *
 * Maps semantic intensities to `navigator.vibrate(...)` patterns.
 * Honors `prefers-reduced-motion: reduce` by silently no-oping.
 *
 * Usage:
 *   const haptic = useHaptic()
 *   haptic.light()   // 10ms tick
 *   haptic.medium()  // 20ms tick
 *   haptic.heavy()   // 30ms tick
 *   haptic.success() // [15, 30, 15]
 *   haptic.error()   // [40, 50, 40]
 */
export function useHaptic() {
  const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function vibrate(pattern: number | number[]) {
    if (prefersReduced) return;
    if (typeof navigator !== "undefined" && navigator.vibrate) {
      navigator.vibrate(pattern);
    }
  }

  return {
    light: () => vibrate(10),
    medium: () => vibrate(20),
    heavy: () => vibrate(30),
    success: () => vibrate([15, 30, 15]),
    error: () => vibrate([40, 50, 40]),
  };
}