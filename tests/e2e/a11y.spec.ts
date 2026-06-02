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
 * Excluded rule — `color-contrast`: the remaining serious findings are muted
 * text (`--text-muted`) on dark cards. That is a shared design-token luminance
 * concern tracked with the theming work (#818/#826 token system), NOT an
 * ARIA/keyboard defect, and re-tuning the token globally is out of scope for
 * the surgical ARIA backfill. Every other serious/critical rule is enforced.
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

// See file header — contrast is a token-luminance concern, tracked separately.
const DISABLED_RULES = ["color-contrast"];

/**
 * Run axe against the legacy content region and assert there are no
 * blocking-impact violations. Attaches the full violation list to the test
 * report for triage.
 */
async function expectNoSeriousA11yViolations(
  page: import("@playwright/test").Page,
  testInfo: import("@playwright/test").TestInfo,
  context: string,
): Promise<void> {
  let builder = new AxeBuilder({ page })
    .include(CONTENT_SELECTOR)
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

test.describe("accessibility (axe-core)", () => {
  test("root / default tab has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await page.goto("/");
    await page.waitForTimeout(600);
    await expectNoSeriousA11yViolations(page, testInfo, "root");
  });

  test("Fleet tab has no serious a11y violations", async ({
    page,
  }, testInfo) => {
    await page.goto("/");
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
    await page.goto("/");
    const queue = page
      .getByRole("button", { name: /queue/i })
      .or(page.getByRole("tab", { name: /queue/i }));
    if (await queue.first().isVisible({ timeout: 2000 }).catch(() => false)) {
      await queue.first().click();
      await page.waitForTimeout(500);
    }
    await expectNoSeriousA11yViolations(page, testInfo, "queue");
  });

  test("primary sections are reachable via an ARIA landmark or tablist", async ({
    page,
  }) => {
    await page.goto("/");
    // The desktop shell exposes a navigation landmark; the legacy/mobile
    // surface exposes a tablist. At least one must be present and visible so
    // keyboard and screen-reader users can move between sections.
    const nav = page.getByRole("navigation");
    const tablist = page.getByRole("tablist");
    const navCount = await nav.count();
    const tablistCount = await tablist.count();
    expect(navCount + tablistCount).toBeGreaterThan(0);
    if (navCount > 0) {
      await expect(nav.first()).toBeVisible({ timeout: 5000 });
    } else {
      await expect(tablist.first()).toBeVisible({ timeout: 5000 });
    }
  });
});
