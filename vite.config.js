import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    root: 'frontend',
    build: {
        outDir: '../dist',
        emptyOutDir: true,
        rollupOptions: {
            output: {
                manualChunks: function (id) {
                    var normalizedId = id.replace(/\\/g, '/');
                    if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
                        return 'vendor-react';
                    }
                    if (id.includes('node_modules/marked')) {
                        return 'vendor-marked';
                    }
                    if (id.includes('node_modules/dompurify')) {
                        return 'vendor-dompurify';
                    }
                    if (normalizedId.includes('/frontend/src/pages/FleetOrchestration')) {
                        return 'fleet-orchestration';
                    }
                },
            },
        },
    },
    server: {
        port: 5173,
        proxy: {
            '/api': 'http://localhost:5001',
        },
    },
});
