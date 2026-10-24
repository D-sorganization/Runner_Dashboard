import { expect, test } from "@playwright/test";

test("Staff loading skeleton fills the content area in both themes", async ({ browser, baseURL }) => {
  const appBaseURL = baseURL ?? "http://localhost:5174";
  const appOrigin = new URL(appBaseURL).origin;
  const staffRoute = new URL("/staff", appBaseURL).toString();

  for (const theme of ["light", "dark"] as const) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      colorScheme: theme,
      serviceWorkers: "block",
    });
    let releaseStaffChunk!: () => void;
    let markStaffChunkRequested!: () => void;
    const staffChunkRequested = new Promise<void>((resolve) => {
      markStaffChunkRequested = resolve;
    });
    const staffChunkGate = new Promise<void>((resolve) => {
      releaseStaffChunk = resolve;
    });
    const page = await context.newPage();
    await page.addInitScript((mode) => {
      localStorage.setItem("runner-dashboard:theme-mode", mode);
    }, theme);
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== appOrigin) return route.abort();
      if (url.pathname.startsWith("/api/")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({}),
        });
      }
      if (/\/(?:src\/pages\/Staff\/StaffPage\.tsx|assets\/StaffPage-[^/]+\.js)$/.test(url.pathname)) {
        markStaffChunkRequested();
        await staffChunkGate;
      }
      return route.continue();
    });

    try {
      await page.goto(staffRoute);
      await staffChunkRequested;
      const fallback = page.getByRole("status", { name: "Loading Staff Console\u2026" });
      await expect(fallback).toBeVisible();
      await page.getByRole("button", { name: "Ask Barb" }).click();
      await expect(page.locator(".desktop-shell--barb-open")).toBeVisible();
      const dimensions = await page.evaluate(() => {
        const main = document.querySelector<HTMLElement>(".desktop-shell__main");
        const loading = document.querySelector<HTMLElement>(".tab-loading-skeleton");
        const card = loading?.firstElementChild as HTMLElement | null;
        if (!main || !loading || !card) throw new Error("Staff fallback layout is missing");
        const style = getComputedStyle(main);
        const contentWidth = main.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        return {
          contentWidth,
          fallbackWidth: loading.getBoundingClientRect().width,
          cardWidth: card.getBoundingClientRect().width,
        };
      });
      expect(dimensions.contentWidth).toBeGreaterThan(700);
      expect(dimensions.fallbackWidth).toBeGreaterThanOrEqual(dimensions.contentWidth - 2);
      expect(dimensions.cardWidth).toBeGreaterThanOrEqual(dimensions.contentWidth - 52);
    } finally {
      releaseStaffChunk();
      await context.close();
    }
  }
});
