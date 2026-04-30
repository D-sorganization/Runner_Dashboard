import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { BreakpointProvider, useBreakpoint } from '../useBreakpoint'
import { breakpoints, getBreakpoint } from '../../design/breakpoints'

/**
 * Captures every MediaQueryList created via window.matchMedia so we can
 * count listener registrations across all consumers. The set of mock
 * MediaQueryList objects is reset before each test.
 */
type MockMql = {
  media: string
  matches: boolean
  onchange: null | ((ev: MediaQueryListEvent) => void)
  addEventListener: ReturnType<typeof vi.fn>
  removeEventListener: ReturnType<typeof vi.fn>
  addListener: ReturnType<typeof vi.fn>
  removeListener: ReturnType<typeof vi.fn>
  dispatchEvent: ReturnType<typeof vi.fn>
}

function installMatchMediaSpy(): MockMql[] {
  const created: MockMql[] = []
  const mqlByMedia = new Map<string, MockMql>()
  window.matchMedia = vi.fn((query: string) => {
    // Reuse the same MockMql per `media` string so `addEventListener`
    // calls land on the same object — that's what real browsers do.
    let mql = mqlByMedia.get(query)
    if (!mql) {
      mql = {
        media: query,
        matches: false,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }
      mqlByMedia.set(query, mql)
      created.push(mql)
    }
    return mql as unknown as MediaQueryList
  }) as unknown as typeof window.matchMedia
  return created
}

function ConsumerProbe({ id }: { id: string }) {
  const bp = useBreakpoint()
  return <span data-testid={`probe-${id}`}>{bp}</span>
}

describe('useBreakpoint / BreakpointProvider', () => {
  let mqls: MockMql[]

  beforeEach(() => {
    mqls = installMatchMediaSpy()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('mounts a single matchMedia listener set regardless of consumer count', () => {
    render(
      <BreakpointProvider>
        <ConsumerProbe id="a" />
        <ConsumerProbe id="b" />
        <ConsumerProbe id="c" />
        <ConsumerProbe id="d" />
        <ConsumerProbe id="e" />
      </BreakpointProvider>,
    )

    // Total listener registrations across every MediaQueryList must equal
    // the number of breakpoint boundaries — i.e. exactly one per boundary,
    // not one per consumer. With 5 consumers and 5 boundaries the bug
    // would have produced 25 registrations.
    const totalListeners = mqls.reduce(
      (sum, mql) => sum + mql.addEventListener.mock.calls.length,
      0,
    )
    const boundaryCount = Object.keys(breakpoints).length
    expect(totalListeners).toBe(boundaryCount)
  })

  it('does NOT register window resize listeners (matchMedia only)', () => {
    const addSpy = vi.spyOn(window, 'addEventListener')
    render(
      <BreakpointProvider>
        <ConsumerProbe id="a" />
      </BreakpointProvider>,
    )
    const resizeRegistrations = addSpy.mock.calls.filter(
      (call) => call[0] === 'resize',
    )
    expect(resizeRegistrations).toHaveLength(0)
  })

  it('removes every listener it added on unmount', () => {
    const { unmount } = render(
      <BreakpointProvider>
        <ConsumerProbe id="a" />
        <ConsumerProbe id="b" />
      </BreakpointProvider>,
    )

    const added = mqls.reduce(
      (sum, mql) => sum + mql.addEventListener.mock.calls.length,
      0,
    )
    unmount()
    const removed = mqls.reduce(
      (sum, mql) => sum + mql.removeEventListener.mock.calls.length,
      0,
    )
    expect(removed).toBe(added)
  })

  it('all consumers receive the same value from the provider', () => {
    const { getByTestId } = render(
      <BreakpointProvider initialBreakpoint="md">
        <ConsumerProbe id="a" />
        <ConsumerProbe id="b" />
        <ConsumerProbe id="c" />
      </BreakpointProvider>,
    )
    // Note: provider runs an effect that re-reads window.innerWidth, so
    // the value may settle to whatever jsdom reports. We just assert
    // every consumer reads the same value.
    const a = getByTestId('probe-a').textContent
    const b = getByTestId('probe-b').textContent
    const c = getByTestId('probe-c').textContent
    expect(a).toBe(b)
    expect(b).toBe(c)
  })
})

describe('getBreakpoint (pure helper)', () => {
  it('returns xs/sm/md/lg/xl based on width thresholds', () => {
    expect(getBreakpoint(320)).toBe('xs')
    expect(getBreakpoint(breakpoints.xs)).toBe('xs')
    expect(getBreakpoint(breakpoints.sm)).toBe('sm')
    expect(getBreakpoint(breakpoints.md)).toBe('md')
    expect(getBreakpoint(breakpoints.lg)).toBe('lg')
    expect(getBreakpoint(breakpoints.lg + 1)).toBe('xl')
    expect(getBreakpoint(breakpoints.xl + 500)).toBe('xl')
  })
})
