import { expect, test, type Page } from "@playwright/test";

const fleetCommandTabs = [
  "Priorities",
  "Proposals",
  "Active work",
  "Messages",
  "Claims",
  "Dispatch",
];

async function installReadOnlyApiFixtures(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const payload = path === "/api/fleet/nodes"
      ? {
        nodes: [
          { name: "node-a-fixture-a", online: true, system: { hostname: "fixture-a" } },
          { name: "node-a-fixture-b", online: true, system: { hostname: "fixture-b" } },
        ],
      }
      : path === "/api/queue/status"
        ? { in_progress: [], queued: [], stale: [], total: 0 }
        : path.startsWith("/api/queue/stale")
          ? { runs: [] }
          : path === "/api/priorities"
            ? { available: true, board: { active: [], deferred: [], disagreements: [] } }
            : path === "/api/priorities/meetings"
              ? { meetings: [] }
              : path === "/api/priorities/directives"
                ? { directives: [], version: "fixture" }
                : path === "/api/proposals"
                  ? { proposals: [] }
                  : path === "/api/coordination/sessions"
                    ? { sessions: [] }
          : path === "/api/v1/agent/overview"
            ? { agents: [], threads: [], activity: [], backlog: { stale_sessions: [], blocked: [] } }
            : path === "/api/v1/staff/roster"
              ? { roles: [{ name: "barb", title: "Barb", summary: "", group: "leadership", valid: true }] }
              : path === "/api/v1/staff/inbox"
                ? { count: 0, counts: { total: 0, approvals: 0, needs_input: 0, escalations: 0 }, sources: {}, generated_at: new Date(0).toISOString(), items: [] }
                : path === "/api/v1/staff/threads" || path.startsWith("/api/v1/staff/threads?")
                  ? { threads: [] }
                  : {};

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
}

async function expectNoPageHorizontalScroll(page: Page, width: number): Promise<void> {
  const metrics = await page.evaluate(() => {
    const main = document.querySelector<HTMLElement>(".mobile-shell__content");
    return {
      documentScroll: document.documentElement.scrollWidth,
      documentClient: document.documentElement.clientWidth,
      mainScroll: main?.scrollWidth ?? 0,
      mainClient: main?.clientWidth ?? 0,
      windowScrollX: window.scrollX,
      overflowingChildren: main ? Array.from(main.querySelectorAll<HTMLElement>("*"))
        .filter((element) => element.getBoundingClientRect().right > main.getBoundingClientRect().right + 1)
        .slice(0, 5)
        .map((element) => ({
          tag: element.tagName,
          className: typeof element.className === "string" ? element.className : "",
          right: Math.round(element.getBoundingClientRect().right),
          width: Math.round(element.getBoundingClientRect().width),
          scrollWidth: element.scrollWidth,
        })) : [],
    };
  });
  expect(metrics.documentScroll, `document horizontal extent at ${width}px: ${JSON.stringify(metrics)}`)
    .toBeLessThanOrEqual(width);
  expect(metrics.mainScroll, `main content horizontal extent at ${width}px: ${JSON.stringify(metrics)}`)
    .toBeLessThanOrEqual(metrics.mainClient);
  expect(metrics.windowScrollX, `page remains at its left edge at ${width}px`).toBe(0);
}

async function expectFirstHeadingInset(page: Page, headingSelector: string): Promise<void> {
  const heading = page.locator(headingSelector).first();
  await expect(heading).toBeVisible();
  const left = await heading.evaluate((element) => element.getBoundingClientRect().left);
  expect(left, "first page heading has at least 16px of left inset").toBeGreaterThanOrEqual(16);
}

const pageRoutes = [
  { path: "/fleet", marker: '[aria-label="Fleet"]', name: "Fleet overview", headingSelector: '[aria-label="Fleet"] h4' },
  { path: "/staff/fleet-command", marker: ".fleet-cmd__title", name: "Fleet Command", headingSelector: ".fleet-cmd__title" },
  { path: "/staff/agent-bus", marker: "main h1", name: "Agent bus", headingSelector: ".agent-bus-page h1" },
  { path: "/work", marker: '[aria-label="Queue and Workflows"]', contentMarker: ".queue-mobile-kpi-strip", name: "Queue" },
  { path: "/staff", marker: '[data-testid="staff-mobile-root"]', name: "Staff", headingSelector: ".staff-mobile__header h1" },
];

test.describe("RD-2986 mobile page layout", () => {
  test.use({ serviceWorkers: "block" });

  for (const width of [375, 390]) {
    test(`keeps audited pages within ${width}px and headings inset`, async ({ page }) => {
      await installReadOnlyApiFixtures(page);
      await page.setViewportSize({ width, height: 844 });

      for (const route of pageRoutes) {
        await page.goto(route.path);
        await expect(page.locator(".mobile-shell__content")).toBeVisible();
        await expect(page.locator(route.marker)).toBeVisible();
        await expectNoPageHorizontalScroll(page, width);
        if (route.headingSelector) {
          await expectFirstHeadingInset(page, route.headingSelector);
        } else if (route.contentMarker) {
          await expect(page.locator(route.contentMarker)).toBeVisible();
        }
      }

      await page.goto("/staff");
      await page.getByRole("button", { name: "Ask Barb" }).click();
      const barb = page.getByRole("dialog", { name: "Ask Barb" });
      await expect(barb).toBeVisible();
      await expectNoPageHorizontalScroll(page, width);
      const barbHeading = barb.locator("h2, h3").first();
      await expect(barbHeading).toBeVisible();
      expect(await barbHeading.evaluate((el) => el.getBoundingClientRect().left)).toBeGreaterThanOrEqual(16);
    });

    test(`Fleet Command subtabs remain reachable with 44px targets at ${width}px`, async ({ page }) => {
      await installReadOnlyApiFixtures(page);
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/staff/fleet-command");
      const tabs = page.getByRole("tablist", { name: "Fleet Command sections" });

      for (const label of fleetCommandTabs) {
        const tab = tabs.getByRole("tab", { name: label, exact: true });
        await expect(tab).toBeVisible();
        await tab.click();
        await expect(tab).toHaveAttribute("aria-selected", "true");
        await expectNoPageHorizontalScroll(page, width);
        const box = await tab.boundingBox();
        expect(box, `${label} has visible bounds`).not.toBeNull();
        expect(box!.height, `${label} target height`).toBeGreaterThanOrEqual(44);
        expect(box!.x, `${label} is reachable from the viewport`).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width, `${label} is visible after selection`).toBeLessThanOrEqual(width);
      }
    });
  }
});
