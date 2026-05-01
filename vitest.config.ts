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
      exclude: ['frontend/src/**/__tests__/**', 'frontend/src/main.tsx'],
      thresholds: {
        lines: 30,
      },
    },
  },
  resolve: {
    alias: {
      // Mirror any aliases configured in vite.config.ts
    },
  },
})
