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
                    if (normalizedId.includes('/frontend/src/lib/api') ||
                        normalizedId.includes('/frontend/src/components/Stat')) {
                        return 'dashboard-runtime';
                    }
                    if (normalizedId.includes('/frontend/src/pages/decompIcons') ||
                        normalizedId.includes('/frontend/src/pages/decompSort') ||
                        normalizedId.includes('/frontend/src/pages/decompSortTh')) {
                        return 'dashboard-decomp';
                    }
                    if (normalizedId.includes('/frontend/src/pages/FleetOrchestration')) {
                        return 'fleet-orchestration';
                    }
                    if (normalizedId.includes('/frontend/src/pages/OverviewPage') ||
                        normalizedId.includes('/frontend/src/pages/FleetTab') ||
                        normalizedId.includes('/frontend/src/pages/OverviewLeases') ||
                        normalizedId.includes('/frontend/src/lib/fleetAlerts') ||
                        normalizedId.includes('/frontend/src/lib/fleetTelemetry') ||
                        normalizedId.includes('/frontend/src/lib/fleetMachines')) {
                        return 'fleet-overview';
                    }
                    if (normalizedId.includes('/frontend/src/pages/RemediationPage') ||
                        normalizedId.includes('/frontend/src/pages/RemediationTab') ||
                        normalizedId.includes('/frontend/src/pages/RemediationPRs') ||
                        normalizedId.includes('/frontend/src/pages/RemediationIssues') ||
                        normalizedId.includes('/frontend/src/pages/remediationDispatch') ||
                        normalizedId.includes('/frontend/src/lib/remediationJules') ||
                        normalizedId.includes('/frontend/src/lib/providerModels')) {
                        return 'remediation';
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
