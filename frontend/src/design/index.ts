// Mobile Design System — barrel exports
// All tokens, primitives, and theme utilities are reachable from here.

export {
  badgeTokens,
  colorTokens,
  surfaceTokens,
  spacingTokens,
  touchTokens,
  cssVariableMap,
  toCssVariables,
} from "./tokens";

export { breakpoints, viewportContracts, isMobile, isCompactMobile, getBreakpoint } from "./breakpoints";

export { typeScale, lineHeights, fontStacks } from "./type";

export { motionDurations, motionEasing, reducedMotionCss, prefersReducedMotion } from "./motion";

export { ThemeProvider } from "./ThemeProvider";
export type { ThemeProviderProps } from "./ThemeProvider";
