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
  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

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
  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

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
  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

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

// ---------------------------------------------------------------------------
// Mobile Staff Console (SC-D8)
// ---------------------------------------------------------------------------

/** Mock the Staff Hub endpoints the mobile console reads (Barb thread + one proposal). */
async function mockStaffApi(page: import("@playwright/test").Page): Promise<void> {
  await page.route("**/api/v1/staff/roster", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        roles: [
          {
            name: "barb",
            title: "Barb",
            summary: "Fleet Orchestrator",
            group: "leadership",
            valid: true,
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/staff/threads/thread-barb-auto/messages", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "msg-user-1", body_md: "Please run disk hygiene" }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          messages: [
            {
              id: "msg-1",
              thread_id: "thread-barb-auto",
              author: "barb",
              author_kind: "staff",
              kind: "text",
              body_md: "Hello! I am Barb.",
              created_at: new Date().toISOString(),
            },
            {
              id: "msg-prop-1",
              thread_id: "thread-barb-auto",
              author: "barb",
              author_kind: "staff",
              kind: "action_proposal",
              body_md: "Trim stale worktrees",
              meta: {
                proposal: {
                  id: "prop-123",
                  thread_id: "thread-barb-auto",
                  action_name: "maintenance.trim_worktrees",
                  description: "Trim stale worktrees",
                  risk_level: "medium",
                  status: "proposed",
                },
              },
              created_at: new Date().toISOString(),
            },
          ],
        }),
      });
    }
  });

  await page.route("**/api/v1/staff/proposals/prop-123/decide", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "approved" }),
    });
  });
}

test.describe("Mobile Staff Console (SC-D8)", () => {
  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

  test("open Barb, send message, approve an action, and open from push deep link", async ({
    page,
  }) => {
    // 1. Mock staff endpoints
    await mockStaffApi(page);

    // 2. Open mobile dashboard and navigate to Staff Console
    await page.goto("/");
    const staffTab = page.locator('[role="tab"]', { hasText: /staff/i });
    if (await staffTab.isVisible()) {
      await staffTab.click();
    }

    // Verify roster view with Ask Barb
    const askBarb = page.locator('[data-testid="staff-mobile-ask-barb"]');
    await expect(askBarb).toBeVisible();

    // 3. Open Barb (transitions full-screen to thread view)
    await askBarb.click();
    const threadView = page.locator('[data-testid="staff-mobile-thread"]');
    await expect(threadView).toBeVisible();
    await expect(page.locator('[data-testid="staff-mobile-back-btn"]')).toBeVisible();

    // 4. Send a message in the safe-area composer
    const composer = page.locator('[data-testid="staff-mobile-composer-container"] textarea');
    await expect(composer).toBeVisible();
    await composer.fill("Please run disk hygiene");
    const sendBtn = page.locator('[data-testid="staff-mobile-composer-container"] button', {
      hasText: /send/i,
    });
    await sendBtn.click();

    // 5. Approve an action proposal
    const approveBtn = page.locator('button:has-text("Approve")').first();
    await expect(approveBtn).toBeVisible();
    await approveBtn.click();

    // 6. Open from push deep link directly to thread
    await page.goto("/staff?thread=thread-barb-auto");
    await expect(page.locator('[data-testid="staff-mobile-thread"]')).toBeVisible();
    await expect(page.locator("text=Conversation with Barb")).toBeVisible();
  });

  test("keyboard walkthrough: open Barb, focus follows the view, back returns to search (SC-D9)", async ({
    page,
  }) => {
    await mockStaffApi(page);
    await page.goto("/staff");

    const askBarb = page.getByTestId("staff-mobile-ask-barb");
    await expect(askBarb).toBeVisible();
    await askBarb.focus();
    await page.keyboard.press("Enter");

    // Opening a thread moves focus to its heading, not the composer.
    await expect(page.getByTestId("staff-mobile-thread")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeFocused();

    // The message log is reachable by keyboard and is a polite live region.
    const log = page.getByRole("log", { name: "Conversation messages" });
    await expect(log).toHaveAttribute("aria-live", "polite");
    await log.focus();
    await expect(log).toBeFocused();

    // Back returns focus to the roster search box.
    await page.getByTestId("staff-mobile-back-btn").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("searchbox", { name: "Search staff roles" })).toBeFocused();
  });
});
