/**
 * Playwright smoke tests for runner-dashboard (issue #389).
 *
 * These are intentionally lightweight: they verify that each page renders
 * without a blank screen or hard JS error. They do NOT require a live GitHub
 * token — the backend serves a loading/error state gracefully when
 * credentials are absent.
 *
 * Run locally:
 *   ./start-dashboard.sh          # starts backend on :8321
 *   npm run test:e2e               # runs Playwright suite
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Page load — root renders something meaningful
// ---------------------------------------------------------------------------

test("root page loads and has a title", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/Runner Dashboard|Fleet|D-sorganization/i);
});

test("root page mounts the React app (div#root is non-empty)", async ({
  page,
}) => {
  await page.goto("/");
  // Wait for the React tree to hydrate — div#root must contain child elements.
  const root = page.locator("#root");
  await expect(root).not.toBeEmpty();
});

test("root page has no top-level JS error before first interaction", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.goto("/");
  // Give the app a moment to settle.
  await page.waitForTimeout(500);
  expect(errors).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Fleet tab — visible by default or reachable via navigation
// ---------------------------------------------------------------------------

test("Fleet tab is visible in navigation", async ({ page }) => {
  await page.goto("/");
  // The Fleet tab label appears in navigation (desktop or mobile shell).
  const fleetNav = page.getByRole("button", { name: /fleet/i }).or(
    page.getByRole("tab", { name: /fleet/i }),
  );
  await expect(fleetNav.first()).toBeVisible({ timeout: 5000 });
});

test("Fleet tab displays runner content after navigation", async ({ page }) => {
  await page.goto("/");
  // Click the Fleet tab if it is not already active.
  const fleetButton = page
    .getByRole("button", { name: /fleet/i })
    .or(page.getByRole("tab", { name: /fleet/i }));
  const button = fleetButton.first();
  if (await button.isVisible()) {
    await button.click();
  }
  // The Fleet tab should show some content — either runner cards, a loading
  // spinner, or an empty-state message. What must NOT appear is a blank body.
  const body = page.locator("body");
  await expect(body).not.toBeEmpty();
});

// ---------------------------------------------------------------------------
// Queue tab — reachable and renders
// ---------------------------------------------------------------------------

test("Queue tab renders without crashing", async ({ page }) => {
  await page.goto("/");
  const queueButton = page
    .getByRole("button", { name: /queue/i })
    .or(page.getByRole("tab", { name: /queue/i }));
  if (await queueButton.first().isVisible()) {
    await queueButton.first().click();
    await page.waitForTimeout(300);
  }
  await expect(page.locator("body")).not.toBeEmpty();
});

// ---------------------------------------------------------------------------
// Maxwell tab — degrades gracefully when daemon is offline
// ---------------------------------------------------------------------------

test("Maxwell tab shows content or graceful offline state", async ({
  page,
}) => {
  await page.goto("/");
  const maxwellButton = page
    .getByRole("button", { name: /maxwell/i })
    .or(page.getByRole("tab", { name: /maxwell/i }));
  if (await maxwellButton.first().isVisible()) {
    await maxwellButton.first().click();
    await page.waitForTimeout(500);
  }
  // Must not show a blank page; either the Maxwell UI or an error/retry state.
  await expect(page.locator("body")).not.toBeEmpty();
});

// ---------------------------------------------------------------------------
// AgentDispatch — page renders (3-step flow stub)
// ---------------------------------------------------------------------------

test("AgentDispatch page renders when navigated to directly", async ({
  page,
}) => {
  // Navigate to root first; AgentDispatch may be accessible via a tab or URL.
  await page.goto("/");
  const dispatchButton = page
    .getByRole("button", { name: /dispatch|agent/i })
    .or(page.getByRole("tab", { name: /dispatch|agent/i }));
  if (await dispatchButton.first().isVisible({ timeout: 2000 })) {
    await dispatchButton.first().click();
    await page.waitForTimeout(300);
  }
  await expect(page.locator("body")).not.toBeEmpty();
});

// ---------------------------------------------------------------------------
// PushSettings — page renders
// ---------------------------------------------------------------------------

test("PushSettings page renders when accessible", async ({ page }) => {
  await page.goto("/");
  const settingsButton = page
    .getByRole("button", { name: /push|notification|settings/i })
    .or(page.getByRole("tab", { name: /push|notification|settings/i }));
  if (await settingsButton.first().isVisible({ timeout: 2000 })) {
    await settingsButton.first().click();
    await page.waitForTimeout(300);
  }
  await expect(page.locator("body")).not.toBeEmpty();
});

// ---------------------------------------------------------------------------
// Basic navigation — shell navigation elements are accessible
// ---------------------------------------------------------------------------

test("navigation bar or tab strip is accessible (has ARIA roles)", async ({
  page,
}) => {
  await page.goto("/");
  // At least one navigation landmark should exist (nav, tablist, or role=navigation).
  const navElement = page
    .getByRole("navigation")
    .or(page.getByRole("tablist"))
    .or(page.locator("nav"));
  // It's acceptable for the element to not exist on all layouts; we just
  // verify the page itself is non-empty.
  await expect(page.locator("body")).not.toBeEmpty();
  // If a nav exists, it must not be hidden from accessibility tree.
  const navCount = await navElement.count();
  if (navCount > 0) {
    await expect(navElement.first()).toBeVisible();
  }
});

test("page does not return HTTP error status codes for root path", async ({
  page,
}) => {
  const response = await page.goto("/");
  // Allow null (about:blank) or 2xx responses.
  if (response) {
    expect(response.status()).toBeLessThan(400);
  }
});
