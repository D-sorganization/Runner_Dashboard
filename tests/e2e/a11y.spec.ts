/**
 * Automated accessibility (axe-core) smoke tests for runner-dashboard.
 *
 * Issue #833: the 17k-line legacy app is the dominant surface and had no
 * automated a11y coverage. These tests run @axe-core/playwright against the
 * primary tab bodies and fail on *serious* / *critical* WCAG violations.
 *
 * They run alongside the existing smoke suite and, like it, require no live
 * GitHub token — the backend serves a loading/error state gracefully when
 * credentials are absent.
 *
 * Scope: analysis is restricted to `#main-content` (the region the shell
 * renders tab bodies into) so the gate targets the legacy/page surface this
 * issue hardens, not the app-shell chrome (navigation sidebar etc.), which is
 * owned and evolved separately.
 *
 * `color-contrast` is now ENFORCED. The muted-text findings that originally
 * forced this rule off were a shared design-token luminance concern (#818/#826);
 * they were resolved by raising `--text-muted` (and auditing `--text-secondary`)
 * to clear WCAG AA 4.5:1 against every surface, in `:root`, `[data-theme="light"]`,
 * and every fleet theme in `frontend/src/design/fleetThemes.ts` (#833/#857).
 *
 * Run locally:
 *   DASHBOARD_PORT=8799 GH_TOKEN=dummy python backend/server.py   # backend
 *   DASHBOARD_URL=http://localhost:8799 npm run test:e2e -- --project=chromium-desktop
 */

import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Only fail the build on the two most severe impact levels. Moderate/minor
// findings (e.g. best-practice landmark hints) are surfaced in the report but
// do not gate CI while the legacy surface is incrementally hardened.
const BLOCKING_IMPACTS = new Set(["serious", "critical"]);

// Region the shell renders tab content into; the focus of issue #833.
const CONTENT_SELECTOR = "#main-content";

// No axe rules are disabled — color-contrast is enforced (see file header).
const DISABLED_RULES: string[] = [];

/**
 * Run axe against the legacy content region and assert there are no
 * blocking-impact violations. Attaches the full violation list to the test
 * report for triage.
 */
async function expectNoSeriousA11yViolations(
  page: import("@playwright/test").Page,
  testInfo: import("@playwright/test").TestInfo,
  context: string,
  include: string = CONTENT_SELECTOR,
): Promise<void> {
  let builder = new AxeBuilder({ page })
    .include(include)
    .withTags(["wcag2a", "wcag2aa"]);
  for (const rule of DISABLED_RULES) {
    builder = builder.disableRules(rule);
  }
  const results = await builder.analyze();

  await testInfo.attach(`axe-${context}.json`, {
    body: JSON.stringify(results.violations, null, 2),
    contentType: "application/json",
  });

  const blocking = results.violations.filter((v) =>
    BLOCKING_IMPACTS.has(v.impact ?? ""),
  );

  const summary = blocking
    .map(
      (v) =>
        `  [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node(s))\n    ${v.helpUrl}`,
    )
    .join("\n");

  expect(
    blocking,
    `Found ${blocking.length} serious/critical a11y violation(s) on ${context}:\n${summary}`,
  ).toEqual([]);
}

/**
 * Navigate to the app and wait for the SPA to actually mount before auditing.
 *
 * `page.goto("/")` only resolves on the `load` event; React then hydrates and
 * renders `#main-content` a tick later. Served as the production bundle (vs. the
 * Vite dev server used in CI) that paint lands slightly later, so a fixed
 * `waitForTimeout` is racy — axe would throw "no elements found for include"
 * when `#main-content` is not attached yet. Wait for the real mount signal
 * (the content region + the shell's nav/tablist) instead.
 */
async function gotoApp(
  page: import("@playwright/test").Page,
  path: string = "/",
): Promise<void> {
  await page.goto(path);
  await page.waitForSelector(CONTENT_SELECTOR, {
    state: "attached",
    timeout: 15000,
  });
  // getByRole resolves implicit ARIA roles (a bare <nav>/<ul role=tablist>),
  // unlike a CSS [role=...] selector which only matches explicit attributes.
  await page
    .getByRole("navigation")
    .or(page.getByRole("tablist"))
    .first()
    .waitFor({ state: "visible", timeout: 15000 });
}

test.describe("accessibility (axe-core)", () => {
  test("root / default tab has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await gotoApp(page);
    await expectNoSeriousA11yViolations(page, testInfo, "root");
  });

  test("Fleet tab has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await gotoApp(page);
    const fleet = page
      .getByRole("button", { name: /fleet/i })
      .or(page.getByRole("tab", { name: /fleet/i }));
    if (await fleet.first().isVisible({ timeout: 2000 }).catch(() => false)) {
      await fleet.first().click();
      await page.waitForTimeout(500);
    }
    await expectNoSeriousA11yViolations(page, testInfo, "fleet");
  });

  test("Queue tab has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await gotoApp(page);
    const queue = page
      .getByRole("button", { name: /queue/i })
      .or(page.getByRole("tab", { name: /queue/i }));
    if (await queue.first().isVisible({ timeout: 2000 }).catch(() => false)) {
      await queue.first().click();
      await page.waitForTimeout(500);
    }
    await expectNoSeriousA11yViolations(page, testInfo, "queue");
  });

  // SC-D9 (#1343): the Staff tab and the '?' shortcut help. Both navigate
  // deterministically and assert the surface is present, so a missing page
  // fails instead of passing an empty scan.
  test("Staff tab has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await gotoApp(page, "/staff");
    await expect(
      page.getByRole("tablist", { name: "Staff sections" }),
    ).toBeVisible({ timeout: 15000 });
    // #1446: the three-pane Staff Console is the default section.
    await expect(
      page.getByRole("region", { name: "Staff conversation" }),
    ).toBeVisible();
    await expectNoSeriousA11yViolations(page, testInfo, "staff");
  });

  test("'?' shortcut help dialog has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await gotoApp(page);
    // The default Staff Console route auto-focuses its composer textarea once
    // the conversation finishes opening, which can land after gotoApp()
    // returns. The '?' shortcut deliberately ignores text fields, so pressing
    // it while the composer holds focus types a literal '?' instead of opening
    // help. Park focus on a non-text control first: the composer only claims
    // focus while document.body is active, so it cannot take it back.
    await page.getByRole("navigation").getByRole("button").first().focus();
    await page.keyboard.press("?");
    const dialog = page.getByRole("dialog", { name: /help/i });
    await expect(dialog).toBeVisible({ timeout: 5000 });
    await expect(dialog).toContainText("Navigate staff roster roles");
    await expectNoSeriousA11yViolations(
      page,
      testInfo,
      "shortcut-help",
      '[role="dialog"]',
    );
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });

  test("primary sections are reachable via an ARIA landmark or tablist", async ({
    page,
  }) => {
    await page.goto("/");
    // The desktop shell exposes a navigation landmark; the legacy/mobile
    // surface exposes a tablist. At least one must be present and visible so
    // keyboard and screen-reader users can move between sections. Use a
    // web-first assertion so it waits for the shell to mount rather than
    // racing the initial paint with an instantaneous count().
    const navOrTablist = page
      .getByRole("navigation")
      .or(page.getByRole("tablist"));
    await expect(navOrTablist.first()).toBeVisible({ timeout: 15000 });
  });

  // #2991: shell chrome (sidebar nav, global toaster) is excluded from the
  // #main-content scan above by design (see file header), so it needs its
  // own targeted coverage rather than widening that gate's scope.
  test("sidebar navigation has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await gotoApp(page);
    const nav = page.getByRole("navigation", { name: "Dashboard sections" });
    if (!(await nav.isVisible({ timeout: 2000 }).catch(() => false))) {
      test.skip(true, "desktop sidebar not present at this viewport");
    }
    await expectNoSeriousA11yViolations(
      page,
      testInfo,
      "sidebar",
      'nav[aria-label="Dashboard sections"]',
    );
  });

  test("notification toaster region has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await gotoApp(page);
    await expectNoSeriousA11yViolations(page, testInfo, "toaster", ".toaster-region");
  });

  // #2991: the Staff mobile "Ask Barb" home view used to render its own
  // <main>, duplicating MobileShell's #main-content landmark.
  test("Staff tab (mobile) has exactly one main landmark", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await gotoApp(page, "/staff");
    await expect(page.getByRole("main")).toHaveCount(1);
  });
});
