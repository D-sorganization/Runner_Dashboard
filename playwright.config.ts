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
import fs from "fs";
import os from "os";
import path from "path";
import { createServer } from "net";
const viewportProfilesRaw = JSON.parse(
  fs.readFileSync(new URL("./tests/frontend/mobile/viewport_profiles.json", import.meta.url), "utf-8")
);

/**
 * Self-hosted CI runners can run multiple Playwright jobs on a shared host
 * network, so a fixed Vite dev-server port collides across jobs (#3738).
 * Ask the OS for a genuinely free ephemeral port instead.
 */
function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address && typeof address === "object") {
        server.close(() => resolve(address.port));
      } else {
        server.close(() => reject(new Error("failed to allocate a free port")));
      }
    });
  });
}

/**
 * Playwright Test re-imports this config module fresh in every worker
 * process it spawns over the life of one job (new worker processes per test
 * file, retries, etc. — even with `workers: 1`). A bare `getFreePort()` call
 * probed a brand-new ephemeral port on every import, so each worker's
 * `BASE_URL` pointed at a different port than the one the single
 * `webServer` process (resolved once, by the master process) actually bound
 * to — every navigation failed with `net::ERR_CONNECTION_REFUSED` (#3763).
 *
 * Cache the first probed port to a job-scoped lock file so every re-import
 * within the same CI job reads back the same port. The job-scoped key keeps
 * concurrent jobs on a shared self-hosted runner from colliding on the same
 * port, which is the original #3738 problem this must not reopen.
 */
async function getStableCiPort(): Promise<number> {
  const jobKey = [
    process.env.GITHUB_RUN_ID ?? "local",
    process.env.GITHUB_RUN_ATTEMPT ?? "1",
    process.env.GITHUB_JOB ?? "job",
  ].join("-");
  const lockPath = path.join(os.tmpdir(), `rd-playwright-vite-port-${jobKey}.txt`);
  if (fs.existsSync(lockPath)) {
    const cached = parseInt(fs.readFileSync(lockPath, "utf-8"), 10);
    if (Number.isInteger(cached) && cached > 0) {
      return cached;
    }
  }
  const port = await getFreePort();
  fs.writeFileSync(lockPath, String(port));
  return port;
}

const VITE_PORT = process.env.CI ? await getStableCiPort() : 5173;

// In CI, the dev-server port is only known after getFreePort() runs above,
// so the test baseURL must follow it rather than a passed-in DASHBOARD_URL.
const BASE_URL = process.env.CI
  ? `http://localhost:${VITE_PORT}`
  : (process.env.DASHBOARD_URL ?? "http://localhost:8321");
const WEB_SERVER_ENV = Object.fromEntries(
  Object.entries(process.env).filter(
    (entry): entry is [string, string] => typeof entry[1] === "string",
  ),
);
WEB_SERVER_ENV.VITE_BACKEND_URL =
  process.env.VITE_BACKEND_URL ??
  `http://127.0.0.1:${process.env.DASHBOARD_PORT ?? "5001"}`;

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
  // The Staff Console suite runs its own fake backend: tests/e2e/staff/playwright.config.ts.
  testIgnore: ["staff/**"],
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
          command: `npm run dev -- --port ${VITE_PORT}`,
          env: WEB_SERVER_ENV,
          url: `http://localhost:${VITE_PORT}`,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      }
    : {}),
});
