import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    globals: false,
    include: ['frontend/src/**/__tests__/**/*.{test,spec}.{ts,tsx}'],
    // v8 coverage instrumentation roughly doubles per-test wall time, and the
    // CI runners are slower than dev machines. Synchronous render+query tests
    // (e.g. Sidebar structure) occasionally brush the default 5s per-test
    // timeout under `--coverage` on CI even though they assert nothing async.
    // Lift the per-test timeout to 15s so legitimate tests aren't killed by
    // environmental headroom; this does not relax any assertion.
    testTimeout: 15000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['frontend/src/**/*.{ts,tsx}'],
      exclude: [
        'frontend/src/**/__tests__/**',
        'frontend/src/legacy/**',
        'frontend/src/main.tsx',
      ],
      // Non-legacy coverage floor. legacy/ (the App.tsx still under migration)
      // is excluded above; these gate the migrated TS/TSX surface. Raised from
      // 30 -> 70 lines per issue #832; the secondary metrics are pinned just
      // under their current values to lock in the gains without flaking on
      // minor drift.
      //
      // Each App.tsx decomposition pass (#836) moves a tab body OUT of the
      // excluded legacy/ tree and INTO the measured pages/ surface, growing the
      // function-count denominator. Pass 6 (#875) lands Credentials/Maxwell/
      // RunnerSchedule with their own behaviour tests (each page 88-100% fn
      // covered), but the larger denominator nudges the GLOBAL function ratio
      // to 67.7%. Pin `functions` just under that (67) — same "lock in current"
      // philosophy as the other metrics; the per-pass page tests keep the real
      // floor far higher.
      thresholds: {
        lines: 70,
        statements: 68,
        functions: 67,
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
