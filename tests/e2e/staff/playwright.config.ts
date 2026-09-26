/**
 * Playwright configuration for the hermetic Staff Console e2e suite (#1341).
 *
 * The backend runs against fake provider CLIs (tests/e2e/fakes), so every
 * reply, failure and run outcome is deterministic and nothing reaches a real
 * provider. Run it on Linux (CI, or WSL locally), because the fake `claude` is
 * a shebang script:
 *
 *   npx playwright test -c tests/e2e/staff/playwright.config.ts
 *
 * STAFF_E2E_BACKEND_URL reuses a backend you started yourself with
 * `python3 tests/e2e/fakes/start_staff_backend.py --port <p>`.
 */

import { defineConfig, devices } from "@playwright/test";

const BACKEND_PORT = Number(process.env.STAFF_E2E_BACKEND_PORT ?? "5092");
const BACKEND_URL = process.env.STAFF_E2E_BACKEND_URL ?? `http://127.0.0.1:${BACKEND_PORT}`;
const UI_PORT = Number(process.env.STAFF_E2E_UI_PORT ?? "5174");
const PYTHON = process.env.STAFF_E2E_PYTHON ?? "python3";
// webServer.env replaces the environment, so carry the parent's (Windows sockets need SystemRoot).
const INHERITED_ENV = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
);

// The launched backend's log, relative to the repo root: STAFF_E2E_PYTHON may
// be a `wsl -e` wrapper, where a Windows absolute path would not resolve.
// globalTeardown.ts scans it for outbound fleet/GitHub requests (#1556).
const BACKEND_LOG_FILE_RELATIVE = "test-results/staff-e2e-backend.log";
if (!process.env.STAFF_E2E_BACKEND_URL) {
  process.env.STAFF_E2E_BACKEND_LOG = BACKEND_LOG_FILE_RELATIVE;
}

const backendServer = process.env.STAFF_E2E_BACKEND_URL
  ? []
  : [
      {
        command: `${PYTHON} tests/e2e/fakes/start_staff_backend.py --port ${BACKEND_PORT} --log-file ${BACKEND_LOG_FILE_RELATIVE}`,
        cwd: "../../..",
        env: INHERITED_ENV,
        url: `${BACKEND_URL}/api/health`,
        reuseExistingServer: false,
        timeout: 90_000,
        stdout: "pipe" as const,
      },
    ];

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  globalTeardown: "./globalTeardown.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  timeout: 45_000,
  expect: { timeout: 10_000 },

  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://localhost:${UI_PORT}`,
    // The PWA service worker would fetch /api itself, out of reach of the auth route in fixtures.ts.
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  webServer: [
    ...backendServer,
    {
      command: `npx vite --port ${UI_PORT} --strictPort`,
      cwd: "../../..",
      env: { ...INHERITED_ENV, VITE_BACKEND_URL: BACKEND_URL },
      url: `http://localhost:${UI_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 90_000,
    },
  ],
});
