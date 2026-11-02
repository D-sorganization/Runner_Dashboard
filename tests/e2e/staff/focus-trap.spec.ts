import { expect, test, type Page } from "./fixtures";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { operatorHeaders } from "./identity";

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

test("mobile focus preserves native order while skipping hidden and closed-detail stops", async ({ page }) => {
  const drawer = await openDrawerWithThread(page, 390, RESTORED_THREAD);
  const baselineLast = drawer.locator(FOCUSABLE).filter({ visible: true }).last();
  await baselineLast.evaluate((element) => element.setAttribute("data-baseline-last", ""));
  await drawer.evaluate((panel) => {
    const fixture = document.createElement("div");
    fixture.innerHTML = '<button tabindex="1">First positive</button><button tabindex="2">Second positive</button><input type="radio" name="checked" aria-label="checked first"><input type="radio" name="checked" aria-label="checked selected" checked><div aria-hidden="true"><button tabindex="0">Hidden stop</button></div><div contenteditable="true" tabindex="0">Editable</div><details open><summary>Open summary</summary></details><details><summary>Closed summary</summary><button>Closed child</button></details><input type="radio" name="unchecked" aria-label="unchecked first"><input type="radio" name="unchecked" aria-label="unchecked second">';
    panel.prepend(fixture);
  });
  const elements = [
    drawer.getByRole("button", { name: "First positive" }),
    drawer.getByRole("button", { name: "Second positive" }),
    drawer.getByRole("radio", { name: "checked selected" }),
    drawer.locator('[contenteditable="true"]'),
    drawer.getByText("Open summary", { exact: true }),
    drawer.getByText("Closed summary", { exact: true }),
    drawer.getByRole("radio", { name: "unchecked first" }),
  ];
  await elements[0].focus();
  await expect(elements[0]).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(drawer.locator("[data-baseline-last]")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(elements[0]).toBeFocused();
  for (const element of elements.slice(1)) {
    await page.keyboard.press("Tab");
    await expect(element).toBeFocused();
  }
  const next = drawer.getByRole("button", { name: "New chat" });
  await page.keyboard.press("Tab");
  await expect(next).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(elements.at(-1)!).toBeFocused();
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

test("captures sanitized drawer screenshots at desktop and phone sizes", async ({ page, request }, testInfo) => {
  if (process.env.CI !== "true" || process.env.STAFF_E2E_BACKEND_URL) {
    throw new Error("Screenshot capture requires the isolated CI fake backend and UI servers");
  }
  const backendPort = Number(process.env.STAFF_E2E_BACKEND_PORT);
  if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) {
    throw new Error("Screenshot capture requires a valid allocated fake-backend port");
  }
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  const title = "Synthetic screenshot conversation";
  const created = await request.post(`${backendUrl}${THREADS_PATH}`, {
    headers: { ...operatorHeaders, "X-Requested-With": "XMLHttpRequest" },
    data: { role: "barb", kind: "auto", title },
  });
  expect(created.ok()).toBeTruthy();
  const createdThread = await created.json();
  expect(createdThread.title).toBe(title);
  expect(typeof createdThread.id).toBe("string");
  const threadId = createdThread.id as string;

  const masks = [
    page.locator('[data-testid="sidebar-footer"]'),
    page.locator("#barb-sidebar nav.project-starter-actions button"),
  ];
  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.width === 390 ? 844 : 900 });
    await page.goto(`/fleet?role=barb&thread=${encodeURIComponent(threadId)}`);
    const listResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "GET" && isBarbThreadList(new URL(response.url())),
    );
    const detailPath = `${THREADS_PATH}/${threadId}`;
    const detailResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "GET" && new URL(response.url()).pathname === detailPath,
    );
    const streamPath = `${detailPath}/stream`;
    const streamResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "GET" && new URL(response.url()).pathname === streamPath,
    );
    const trigger = page.getByTestId(viewport.width === 390 ? "mobile-ask-barb" : "ask-barb-toggle");
    await expect(trigger).toBeVisible();
    await trigger.click();
    const listResponse = await listResponsePromise;
    expect(listResponse.status()).toBe(200);
    expect((await listResponse.json()).threads.map((thread: { id: string }) => thread.id)).toContain(threadId);
    const detailResponse = await detailResponsePromise;
    expect(detailResponse.status()).toBe(200);
    expect(await detailResponse.json()).toMatchObject({ thread: { id: threadId, title }, messages: [] });
    const streamResponse = await streamResponsePromise;
    expect(streamResponse.status()).toBe(200);
    expect(await streamResponse.headerValue("content-type")).toContain("text/event-stream");

    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    if (viewport.width === 1280) {
      const actions = page.locator(".desktop-shell__actions");
      const sidebarFooter = page.locator('[data-testid="sidebar-footer"]');
      await expect(actions).toHaveCount(1);
      await expect(sidebarFooter).toHaveCount(1);
      await expect(sidebarFooter).toBeVisible();
      expect(await actions.evaluate((element) =>
        !element.contains(document.querySelector("#barb-sidebar")),
      )).toBe(true);
      expect(await drawer.evaluate((element) => !element.closest(".desktop-shell__actions"))).toBe(true);
    }
    await expect(drawer.getByRole("region", { name: `Conversation thread with ${title}` })).toBeVisible();
    await expect(drawer.getByText("Reconnecting to thread events…", { exact: true })).toHaveCount(0);
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
          .desktop-shell__actions, .desktop-shell__actions *,
          .desktop-shell__actions::before, .desktop-shell__actions::after,
          .desktop-shell__actions *::before, .desktop-shell__actions *::after {
            visibility: hidden !important;
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
