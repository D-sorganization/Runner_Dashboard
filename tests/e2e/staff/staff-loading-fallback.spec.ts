import { expect, test } from "./fixtures";

test("Staff loading skeleton fills the content area in both themes", async ({ browser, baseURL }) => {
  if (!baseURL) {
    throw new Error("Staff loading fallback test requires Playwright config use.baseURL.");
  }
  const appBaseURL = baseURL;
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

test("a failed Barb chunk stays local while closed and recovers after reload", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("Barb failure test requires Playwright config use.baseURL.");
  const appOrigin = new URL(baseURL).origin;
  const apiOrigin = new URL(
    process.env.STAFF_E2E_BACKEND_URL ?? `http://127.0.0.1:${process.env.STAFF_E2E_BACKEND_PORT ?? "5092"}`,
  ).origin;
  let releaseChunk!: () => void;
  let chunkAborted = false;
  const chunkGate = new Promise<void>((resolve) => { releaseChunk = resolve; });

  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== appOrigin && url.origin !== apiOrigin) return route.abort();
    if (!chunkAborted && /\/(?:src\/shell\/BarbSidebar\.tsx|assets\/BarbSidebar-[^/]+\.js)$/.test(url.pathname)) {
      chunkAborted = true;
      await chunkGate;
      return route.abort("failed");
    }
    return route.fallback();
  });

  try {
    await page.goto("/");
    const heading = page.getByRole("heading", { name: "What would you like to get done?" });
    const toggle = page.getByRole("button", { name: "Ask Barb" });
    await expect(heading).toBeVisible();
    await expect(toggle).toBeVisible();

    const failedRequest = page.waitForEvent("requestfailed", (request) => /BarbSidebar/.test(request.url()));
    const chunkRequested = page.waitForRequest((request) => /BarbSidebar/.test(request.url()));
    await toggle.hover();
    await chunkRequested;
    await toggle.click();
    const panel = page.locator("#barb-sidebar");
    await page.getByRole("button", { name: "Close Ask Barb" }).click();
    releaseChunk();
    await failedRequest;

    await expect(heading).toBeVisible();
    await expect(panel).toHaveAttribute("aria-hidden", "true");
    await expect(panel).toHaveAttribute("inert");
    await toggle.click();
    await expect(panel.getByRole("alert")).toContainText("Ask Barb could not load");
    await page.keyboard.press("Escape");
    await expect(panel).toHaveAttribute("aria-hidden", "true");
    await expect(panel).toHaveAttribute("inert");
    await expect(heading).toBeVisible();
    await toggle.click();
    await expect(panel.getByRole("alert")).toContainText("Ask Barb could not load");

    await page.getByRole("button", { name: "Reload dashboard" }).click();
    await page.getByRole("button", { name: "Ask Barb" }).click();
    const composer = page.locator("#barb-sidebar").getByRole("textbox", { name: "Staff conversation input" });
    await expect(composer).toBeVisible();
    await composer.fill("Recovered conversation");
    await expect(composer).toHaveValue("Recovered conversation");
  } finally {
    releaseChunk();
  }
});
