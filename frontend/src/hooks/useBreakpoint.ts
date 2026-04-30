import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { breakpoints, getBreakpoint } from '../design/breakpoints'

type BreakpointKey = keyof typeof breakpoints

const SSR_DEFAULT_WIDTH = 1280

/**
 * Sorted ascending list of the breakpoint upper bounds we care about.
 * We create one matchMedia query per boundary; whichever fires `change`
 * just triggers a single recompute of the active key.
 */
const BREAKPOINT_BOUNDARIES: ReadonlyArray<number> = (
  Object.values(breakpoints) as ReadonlyArray<number>
).slice().sort((a, b) => a - b)

const BreakpointContext = createContext<BreakpointKey | null>(null)

function readCurrentBreakpoint(): BreakpointKey {
  if (typeof window === 'undefined') {
    return getBreakpoint(SSR_DEFAULT_WIDTH)
  }
  return getBreakpoint(window.innerWidth)
}

export interface BreakpointProviderProps {
  children: ReactNode
  /**
   * Test-only override: pass an initial value rather than reading
   * `window.innerWidth`. Production callers should leave this unset.
   */
  initialBreakpoint?: BreakpointKey
}

/**
 * BreakpointProvider mounts a single set of MediaQueryList listeners
 * (one per breakpoint boundary, regardless of consumer count) and
 * publishes the current breakpoint key through React context.
 *
 * Wrap the app once near the root. Every `useBreakpoint()` consumer
 * then reads from this single subscription instead of registering its
 * own resize/matchMedia listener.
 */
export function BreakpointProvider({
  children,
  initialBreakpoint,
}: BreakpointProviderProps) {
  const [breakpoint, setBreakpoint] = useState<BreakpointKey>(
    () => initialBreakpoint ?? readCurrentBreakpoint(),
  )
  // Track listener count for tests / debug — not used at runtime.
  const listenerCountRef = useRef(0)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return
    }

    const recompute = () => {
      setBreakpoint(getBreakpoint(window.innerWidth))
    }

    // One MediaQueryList per breakpoint boundary. A change at *any*
    // boundary triggers a single recompute of the active key.
    const mediaQueryLists = BREAKPOINT_BOUNDARIES.map((bound) =>
      window.matchMedia(`(min-width: ${bound + 1}px)`),
    )

    for (const mql of mediaQueryLists) {
      mql.addEventListener('change', recompute)
      listenerCountRef.current += 1
    }

    // Run once on mount so we sync with the actual current viewport.
    recompute()

    return () => {
      for (const mql of mediaQueryLists) {
        mql.removeEventListener('change', recompute)
        listenerCountRef.current -= 1
      }
    }
  }, [])

  return createElement(
    BreakpointContext.Provider,
    { value: breakpoint },
    children,
  )
}

/**
 * Returns the live breakpoint key. Must be rendered inside a
 * `<BreakpointProvider>`. If no provider is mounted we fall back to a
 * one-time SSR-safe value and warn in development to flag the missing
 * provider — this keeps the hook safe to call without crashing while
 * making the misuse visible.
 */
export function useBreakpoint(): BreakpointKey {
  const ctx = useContext(BreakpointContext)
  // One-time fallback (read once, no subscription) when no provider exists.
  const fallback = useMemo(() => readCurrentBreakpoint(), [])

  if (ctx === null) {
    // Dev-only warning. We avoid depending on @types/node by reading
    // `process.env.NODE_ENV` through a guarded `globalThis` access.
    const proc = (globalThis as unknown as {
      process?: { env?: { NODE_ENV?: string } }
    }).process
    if (proc && proc.env && proc.env.NODE_ENV !== 'production') {
      // eslint-disable-next-line no-console
      console.warn(
        '[useBreakpoint] No <BreakpointProvider> found in the tree. ' +
          'Falling back to a static value; the hook will not update on resize.',
      )
    }
    return fallback
  }
  return ctx
}
