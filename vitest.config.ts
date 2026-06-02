import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    globals: false,
    include: ['frontend/src/**/__tests__/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['frontend/src/**/*.{ts,tsx}'],
      exclude: [
        'frontend/src/**/__tests__/**',
        'frontend/src/legacy/**',
        'frontend/src/main.tsx',
      ],
      // Non-legacy coverage floor. legacy/ (the 17k-line App.tsx still under
      // migration) is excluded above; these gate the migrated TS/TSX surface.
      // Raised from 30 -> 70 lines per issue #832; the secondary metrics are
      // pinned just under their current values to lock in the gains without
      // flaking on minor branch-count drift.
      thresholds: {
        lines: 70,
        statements: 68,
        functions: 68,
        branches: 59,
      },
    },
  },
  resolve: {
    alias: {
      // Mirror any aliases configured in vite.config.ts
    },
  },
})
