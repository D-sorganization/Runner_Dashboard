import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendProxyTarget =
  process.env.VITE_BACKEND_URL ?? `http://127.0.0.1:${process.env.DASHBOARD_PORT ?? '5001'}`

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  root: 'frontend',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          const normalizedId = id.replace(/\\/g, '/')
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'vendor-react'
          }
          if (id.includes('node_modules/marked')) {
            return 'vendor-marked'
          }
        if (id.includes('node_modules/dompurify')) {
          return 'vendor-dompurify'
        }
        if (normalizedId.includes('/frontend/src/pages/FleetOrchestration')) {
          return 'fleet-orchestration'
        }
      },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': backendProxyTarget,
    },
  },
})
