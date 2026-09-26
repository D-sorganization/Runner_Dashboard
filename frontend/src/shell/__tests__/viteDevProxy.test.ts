// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { resolveConfig } from 'vite'

describe('vite dev server config resolution (#1549)', () => {
  it('resolves vite.config.ts as the active config file (not stale vite.config.js)', async () => {
    const config = await resolveConfig({}, 'serve')
    expect(config.configFile).toBeDefined()
    expect(config.configFile?.replace(/\\/g, '/')).toMatch(/vite\.config\.ts$/)
  })
})
