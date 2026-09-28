import { describe, expect, it } from 'vitest'
import { resolveBuildId } from '../buildId'

// #1740: the service worker is registered as /sw.js?build=<id>. A constant id
// ("dev") means the worker never updates after a deploy, so the "update ready"
// toast never fires and the worker cache never rotates.
describe('resolveBuildId', () => {
  it('prefers an explicit VITE_BUILD_ID', () => {
    expect(resolveBuildId({ explicit: 'rel-42', gitSha: 'abc1234', now: 1 })).toBe('rel-42')
  })

  it('falls back to the git short SHA', () => {
    expect(resolveBuildId({ explicit: undefined, gitSha: 'abc1234\n', now: 1 })).toBe('abc1234')
  })

  it('falls back to the build time when git is unavailable, never "dev"', () => {
    const id = resolveBuildId({ explicit: '  ', gitSha: null, now: Date.UTC(2026, 8, 28, 14, 0, 0) })
    expect(id).toBe('t1790604000000')
    expect(id).not.toBe('dev')
  })
})
