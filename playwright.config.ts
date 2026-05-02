/**
 * Playwright configuration for runner-dashboard e2e smoke tests (issue #389).
 *
 * Tests run against the dashboard served at http://localhost:8321. Start the
 * backend with `./start-dashboard.sh` (or the CI webServer config below)
 * before running `npm run test:e2e`.
 *
 * Viewport profiles are drawn from tests/frontend/mobile/viewport_profiles.json
 * to keep the mobile smoke tests and the Playwright suite in sync.
 */

import { defineConfig, devices } from "@playwright/test";
import viewportProfilesRaw from "./tests/frontend/mobile/viewport_profiles.json";

const BASE_URL = process.env.DASHBOARD_URL ?? "http://localhost:8321";

/** Desktop project — standard 1280×720 Chromium. */
const desktopProject = {
  name: "chromium-desktop",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: BASE_URL,
  },
};

/** Mobile projects derived from viewport_profiles.json. */
const mobileProjects = viewportProfilesRaw.profiles.map((p) => ({
  name: `chromium-${p.name}`,
  use: {
    browserName: viewportProfilesRaw.playwright.browserName as "chromium",
    headless: viewportProfilesRaw.playwright.headless,
    hasTouch: viewportProfilesRaw.playwright.hasTouch,
    isMobile: viewportProfilesRaw.playwright.isMobile,
    viewport: p.viewport,
    deviceScaleFactor: p.deviceScaleFactor,
    baseURL: BASE_URL,
  },
}));

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "on-first-retry",
  },

  projects: [desktopProject, ...mobileProjects],

  // In CI, spin up Vite dev server pointing at the running backend.
  // For local development, start the backend manually with ./start-dashboard.sh.
  ...(process.env.CI
    ? {
        webServer: {
          command: "npm run dev -- --port 5173",
          url: "http://localhost:5173",
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      }
    : {}),
});
