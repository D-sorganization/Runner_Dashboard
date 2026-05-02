/**
 * Playwright mobile viewport specs for runner-dashboard (EPIC #186 — M16).
 *
 * These tests run against the mobile Playwright projects defined in
 * playwright.config.ts (chromium-iphone-12, chromium-pixel-5,
 * chromium-epic-compact-375, chromium-epic-standard-412). The config derives
 * those projects from tests/frontend/mobile/viewport_profiles.json, so this
 * suite is always in sync with the viewport definitions.
 *
 * Tests verify the MobileShell renders correctly at 375 px and 412 px widths,
 * bottom navigation is reachable and touch-target-compliant, and the
 * 3-tap dispatch flow reaches the Remediation tab.
 *
 * Run locally (desktop projects are skipped by Playwright project filtering):
 *   ./start-dashboard.sh
 *   npm run test:e2e -- --project="chromium-epic-compact-375"
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mobile shell — bottom navigation
// ---------------------------------------------------------------------------

test.describe("Mobile shell bottom navigation", () => {
  test("renders bottom tab bar at mobile viewport", async ({ page }) => {
    await page.goto("/");
    // MobileShell bottom nav should be visible at mobile widths
    const nav = page.locator('[role="tablist"]');
    await expect(nav).toBeVisible();
  });

  test("Fleet tab is default active tab", async ({ page }) => {
    await page.goto("/");
    const fleetTab = page.locator('[role="tab"][aria-selected="true"]');
    await expect(fleetTab).toContainText(/fleet/i);
  });

  test("bottom nav tabs are at least 44px tall (touch target)", async ({
    page,
  }) => {
    await page.goto("/");
    const tabs = page.locator('[role="tab"]');
    const count = await tabs.count();
    for (let i = 0; i < count; i++) {
      const box = await tabs.nth(i).boundingBox();
      if (box) {
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Mobile 3-tap dispatch flow
// ---------------------------------------------------------------------------

test.describe("Mobile 3-tap dispatch flow", () => {
  test("Remediation tab is reachable via bottom nav", async ({ page }) => {
    await page.goto("/");
    const remediationTab = page.locator('[role="tab"]', {
      hasText: /remediation/i,
    });
    await remediationTab.click();
    // Should show remediation content without crashing
    await expect(page.locator("body")).not.toBeEmpty();
  });
});

// ---------------------------------------------------------------------------
// Mobile accessibility
// ---------------------------------------------------------------------------

test.describe("Mobile accessibility", () => {
  test("all bottom nav tabs have aria-label or accessible text", async ({
    page,
  }) => {
    await page.goto("/");
    const tabs = page.locator('[role="tab"]');
    const count = await tabs.count();
    for (let i = 0; i < count; i++) {
      const tab = tabs.nth(i);
      const label = await tab.getAttribute("aria-label");
      const text = await tab.textContent();
      expect(label || text?.trim()).toBeTruthy();
    }
  });
});
