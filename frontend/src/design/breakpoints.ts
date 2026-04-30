export const breakpoints = {
  xs: 360,
  sm: 390,
  md: 768,
  lg: 1024,
  xl: 1280,
} as const;

export const viewportContracts = {
  iphoneCompact: { width: 375, height: 812 },
  pixelStandard: { width: 412, height: 915 },
} as const;

export function isMobile(width: number): boolean {
  return width <= breakpoints.md;
}

export function isCompactMobile(width: number): boolean {
  return width <= breakpoints.sm;
}

/**
 * Pure helper that maps a viewport width to a breakpoint key.
 *
 * Note: this is intentionally NOT a runtime hook. It does not subscribe to
 * resize/matchMedia events. For the live, reactive value, use the
 * `useBreakpoint()` hook from `frontend/src/hooks/useBreakpoint.ts`.
 */
export function getBreakpoint(width: number): keyof typeof breakpoints {
  if (width <= breakpoints.xs) return "xs";
  if (width <= breakpoints.sm) return "sm";
  if (width <= breakpoints.md) return "md";
  if (width <= breakpoints.lg) return "lg";
  return "xl";
}
