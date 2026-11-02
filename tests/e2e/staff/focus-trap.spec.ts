import { expect, test, type Page } from "./fixtures";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

test.use({ trace: "off", screenshot: "off", video: "off" });

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const HIDDEN_KINDS = ["css-visibility", "aria-hidden", "inert"] as const;
const EDGES = ["before-first", "after-last"] as const;
const THREADS_PATH = "/api/v1/staff/threads";
const RESTORED_THREAD = {
  id: "thr_focus_restore",
  title: "Synthetic restored conversation",
  kind: "auto",
  participants: ["user:synthetic", "barb"],
  status: "active",
};

function isBarbThreadList(url: URL): boolean {
  return url.pathname === THREADS_PATH && url.searchParams.get("role") === "barb";
}

async function serveThread(page: Page, thread: typeof RESTORED_THREAD) {
  const detailPath = `${THREADS_PATH}/${thread.id}`;
  const detailMatcher = (url: URL) => url.pathname === detailPath;
  const streamMatcher = (url: URL) => url.pathname === `${detailPath}/stream`;
  await page.route(detailMatcher, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { thread, messages: [] } })
      : route.fallback(),
  );
  await page.route(streamMatcher, (route) =>
    route.request().method() === "GET" ? route.fulfill({ status: 204 }) : route.fallback(),
  );
  return { detailMatcher, streamMatcher };
}

async function openDrawerWithThread(page: Page, width: number, thread: typeof RESTORED_THREAD) {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
  await page.goto("/fleet");

  let listRequests = 0;
  await page.route(isBarbThreadList, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    listRequests += 1;
    await route.fulfill({ json: { threads: [thread] } });
  });
  const detailPath = `${THREADS_PATH}/${thread.id}`;
  const routes = await serveThread(page, thread);

  const listResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" && isBarbThreadList(new URL(response.url())),
  );
  const detailResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" && new URL(response.url()).pathname === detailPath,
  );
  const trigger = page.getByTestId(width === 390 ? "mobile-ask-barb" : "ask-barb-toggle");
  await expect(trigger).toBeVisible();
  await trigger.click();
  const listResponse = await listResponsePromise;
  expect(listResponse.status()).toBe(200);
  expect(await listResponse.json()).toEqual({ threads: [thread] });
  expect(listRequests).toBe(1);
  const detailResponse = await detailResponsePromise;
  expect(detailResponse.status()).toBe(200);
  expect(await detailResponse.json()).toEqual({ thread, messages: [] });

  const drawer = page.getByTestId("barb-sidebar");
  await expect(drawer).toBeVisible();
  const threadRegion = drawer.getByRole("region", {
    name: `Conversation thread with ${thread.title}`,
  });
  await expect(threadRegion).toBeVisible();
  await page.unroute(isBarbThreadList);
  await page.unroute(routes.detailMatcher);
  await page.unroute(routes.streamMatcher);
  return drawer;
}

for (const kind of HIDDEN_KINDS) {
  for (const edge of EDGES) {
    test(`mobile focus wrap skips ${kind} candidate at ${edge}`, async ({ page }) => {
      const drawer = await openDrawerWithThread(page, 390, RESTORED_THREAD);
      await expect(drawer).toHaveAttribute("aria-modal", "true");

      const visible = drawer.locator(FOCUSABLE).filter({ visible: true });
      await visible.first().evaluate((element) => {
        element.setAttribute("data-focus-edge", "first");
      });
      await visible.last().evaluate((element) => {
        element.setAttribute("data-focus-edge", "last");
      });
      const first = drawer.locator('[data-focus-edge="first"]');
      const last = drawer.locator('[data-focus-edge="last"]');
      await drawer.evaluate(
        (panel, { candidateKind, candidateEdge }) => {
          const group = document.createElement("div");
          if (candidateKind === "css-visibility") group.style.visibility = "hidden";
          if (candidateKind === "aria-hidden") group.setAttribute("aria-hidden", "true");
          if (candidateKind === "inert") group.inert = true;
          const candidate = document.createElement("button");
          candidate.dataset.focusCandidate = candidateKind;
          group.append(candidate);
          if (candidateEdge === "before-first") {
            const first = panel.querySelector('[data-focus-edge="first"]');
            first?.parentNode?.insertBefore(group, first);
          } else {
            panel.append(group);
          }
        },
        { candidateKind: kind, candidateEdge: edge },
      );
      await expect(drawer.locator(`[data-focus-candidate="${kind}"]`)).toHaveCount(1);

      await first.focus();
      await expect(first).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      let shiftWrapFocused = true;
      try {
        await expect(last).toBeFocused();
      } catch {
        shiftWrapFocused = false;
      }

      await last.focus();
      await expect(last).toBeFocused();
      await page.keyboard.press("Tab");
      let tabWrapFocused = true;
      try {
        await expect(first).toBeFocused();
      } catch {
        tabWrapFocused = false;
      }
      expect({ shiftWrapFocused, tabWrapFocused }).toEqual({
        shiftWrapFocused: true,
        tabWrapFocused: true,
      });
    });
  }
}

test("mobile focus skips an aria-hidden candidate inside the tab order", async ({ page }) => {
  const drawer = await openDrawerWithThread(page, 390, RESTORED_THREAD);

  const visible = drawer.locator(FOCUSABLE).filter({ visible: true });
  await visible.first().evaluate((element) => {
    element.setAttribute("data-focus-interior", "first");
  });
  await visible.nth(1).evaluate((element) => {
    element.setAttribute("data-focus-interior", "next");
  });
  const first = drawer.locator('[data-focus-interior="first"]');
  const next = drawer.locator('[data-focus-interior="next"]');
  await drawer.evaluate((panel) => {
    const next = panel.querySelector('[data-focus-interior="next"]');
    if (!next?.parentNode) throw new Error("missing next focus candidate");
    const group = document.createElement("div");
    group.setAttribute("aria-hidden", "true");
    const candidate = document.createElement("button");
    candidate.dataset.focusCandidate = "interior-aria-hidden";
    group.append(candidate);
    next.parentNode.insertBefore(group, next);
  });
  await expect(drawer.locator('[data-focus-candidate="interior-aria-hidden"]')).toHaveCount(1);

  await first.focus();
  await expect(first).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(next).toBeFocused();
});

test("passive thread restore preserves moved focus; New chat keeps explicit focus", async ({ page }) => {
  const restored = RESTORED_THREAD;
  const explicit = {
    ...restored,
    id: "thr_focus_explicit",
    title: "Synthetic explicit conversation",
  };
  let releaseList!: () => void;
  let signalListStarted!: () => void;
  const listGate = new Promise<void>((resolve) => {
    releaseList = resolve;
  });
  const listStarted = new Promise<void>((resolve) => {
    signalListStarted = resolve;
  });

  let listRequests = 0;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/fleet");
  await page.route(isBarbThreadList, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    listRequests += 1;
    signalListStarted();
    await listGate;
    await route.fulfill({ json: { threads: [restored] } });
  });
  for (const thread of [restored, explicit]) await serveThread(page, thread);
  await page.route((url) => url.pathname === THREADS_PATH, async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ json: explicit });
    return route.fallback();
  });

  try {
    const listResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "GET" && isBarbThreadList(new URL(response.url())),
    );
    const restoredDetailPromise = page.waitForResponse((response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === `${THREADS_PATH}/${restored.id}`,
    );
    await page.getByTestId("mobile-ask-barb").click();
    await listStarted;
    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    const retainedFocus = drawer.getByRole("button", { name: "Open Projects" });
    const close = drawer.getByRole("button", { name: "Close Ask Barb" });
    await expect(close).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(drawer.getByRole("button", { name: "Open Fleet Command" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(retainedFocus).toBeFocused();

    releaseList();
    const listResponse = await listResponsePromise;
    expect(listResponse.status()).toBe(200);
    expect(await listResponse.json()).toEqual({ threads: [restored] });
    expect(listRequests).toBe(1);
    const restoredDetail = await restoredDetailPromise;
    expect(restoredDetail.status()).toBe(200);
    expect(await restoredDetail.json()).toEqual({ thread: restored, messages: [] });
    await expect(drawer.getByRole("region", {
      name: `Conversation thread with ${restored.title}`,
    })).toBeVisible();
    await expect(retainedFocus).toBeFocused();

    const explicitDetailPromise = page.waitForResponse((response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === `${THREADS_PATH}/${explicit.id}`,
    );
    await drawer.getByRole("button", { name: "New chat" }).click();
    const explicitDetail = await explicitDetailPromise;
    expect(explicitDetail.status()).toBe(200);
    await expect(drawer.getByRole("region", {
      name: `Conversation thread with ${explicit.title}`,
    })).toBeVisible();
    await expect(drawer.getByRole("textbox", { name: "Staff conversation input" })).toBeFocused();
  } finally {
    releaseList();
  }
});

test("captures sanitized drawer screenshots at desktop and phone sizes", async ({ page }, testInfo) => {
  const masks = [
    page.locator(".mobile-shell__header"),
    page.locator(".mobile-shell__nav"),
    page.locator(".desktop-shell__topbar"),
    page.locator('nav[aria-label="Dashboard sections"]'),
  ];
  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const drawer = await openDrawerWithThread(page, viewport.width, RESTORED_THREAD);
    const mainSelector = viewport.width === 390
      ? ".mobile-shell__content"
      : ".desktop-shell__main";
    const main = page.locator(mainSelector);
    await expect(main).toHaveCount(1);
    await expect(main).toBeVisible();
    expect(await drawer.evaluate((element, selector) => !element.closest(selector), mainSelector)).toBe(true);
    const capture = async (state: string) => {
      await expect(drawer).toBeVisible();
      const screenshot = await page.screenshot({
        animations: "disabled",
        mask: masks,
        maskColor: "#000000",
        style: `
          .desktop-shell__main, .mobile-shell__content {
            color: transparent !important;
            -webkit-text-fill-color: transparent !important;
            text-shadow: none !important;
            background-image: none !important;
            box-shadow: none !important;
          }
          .desktop-shell__main *, .mobile-shell__content *,
          .desktop-shell__main::before, .desktop-shell__main::after,
          .mobile-shell__content::before, .mobile-shell__content::after,
          .desktop-shell__main *::before, .desktop-shell__main *::after,
          .mobile-shell__content *::before, .mobile-shell__content *::after {
            visibility: hidden !important;
          }
        `,
      });
      const filename = `drawer-${viewport.width}-${state}.png`;
      const output = resolve("test-results", "baseline-screenshots");
      await mkdir(output, { recursive: true });
      await writeFile(join(output, filename), screenshot);
      await testInfo.attach(`drawer-${viewport.width}-${state}`, {
        body: screenshot,
        contentType: "image/png",
      });
    };
    await capture("restored-default-focus");
    const composer = drawer.getByRole("textbox", { name: "Staff conversation input" });
    await composer.focus();
    await expect(composer).toBeFocused();
    await capture("composer-focused");
  }
});
