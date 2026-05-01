/**
 * Minimal smoke test that proves the vitest + jsdom setup is wired correctly.
 * This test must always pass and must not be deleted — it is the health check
 * for the frontend test infrastructure introduced in issue #336.
 */
import { describe, it, expect, vi } from 'vitest'

describe('vitest setup smoke test', () => {
  it('vi.fn() creates a spy that records calls', () => {
    const spy = vi.fn((x: number) => x * 2)
    expect(spy(3)).toBe(6)
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith(3)
  })

  it('jsdom is available as the test environment', () => {
    expect(typeof document).toBe('object')
    expect(typeof window).toBe('object')
    const el = document.createElement('div')
    el.textContent = 'hello'
    expect(el.textContent).toBe('hello')
  })
})
