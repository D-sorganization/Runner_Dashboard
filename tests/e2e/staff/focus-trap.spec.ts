import type {
  APIRequestContext,
  Page,
  Request,
  Response,
  Route,
  TestInfo,
} from "@playwright/test";
import { expect, test } from "./fixtures";
import {
  attachSidebarFocusState,
  expectFocusWithState,
  FailureRequestSummary,
  isBarbThreadList,
  SIDEBAR_FOCUSABLE,
} from "./focusTrapDiagnostics";
import { operatorHeaders } from "./identity";

const BARB_THREAD_LIST = /\/api\/v1\/staff\/threads\?role=barb(?:&|$)/;

async function seedBarbThread(
  request: APIRequestContext,
  title: string,
): Promise<void> {
  const backend =
    process.env.STAFF_E2E_BACKEND_URL ??
    `http://127.0.0.1:${process.env.STAFF_E2E_BACKEND_PORT ?? "5092"}`;
  const response = await request.post(`${backend}/api/v1/staff/threads`, {
    headers: { ...operatorHeaders, "X-Requested-With": "XMLHttpRequest" },
    data: { role: "barb", kind: "auto", title },
  });
  expect(response.ok()).toBe(true);
}

function deferred(): { promise: Promise<void>; release: () => void } {
  let release = () => {};
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

test("failure summaries are bounded, sanitized, and fail-safe", async ({
  page,
}, testInfo) => {
  const callbacks = new Map<string, (value: unknown) => void>();
  const fakePage = {
    on: (event: string, callback: (value: unknown) => void) =>
      callbacks.set(event, callback),
    off: (event: string) => callbacks.delete(event),
  } as unknown as Page;
  const summary = new FailureRequestSummary(3);
  summary.watch(fakePage);
  const sendRequest = (method: string, url: string) => {
    const request = {
      method: () => method,
      url: () => url,
    } as unknown as Request;
    callbacks.get("request")?.(request);
    return request;
  };
  const thread = sendRequest(
    "GET",
    "http://localhost/api/v1/staff/threads?role=barb&id=private-canary",
  );
  callbacks.get("response")?.({
    request: () => thread,
    status: () => 200,
  } as unknown as Response);
  sendRequest("BREW", "http://localhost/api/private/private-canary?secret=raw");
  const proposal = sendRequest(
    "POST",
    "http://localhost/api/v1/staff/proposals/private-canary/decide?secret=raw",
  );
  callbacks.get("requestfailed")?.(proposal);
  sendRequest("GET", "http://localhost/api/overflow");
  const snapshot = summary.snapshot();
  expect(snapshot.dropped).toBe(1);
  expect(
    snapshot.requests.map(({ route, method, state, status }) => ({
      route,
      method,
      state,
      status,
    })),
  ).toEqual([
    { route: "staff.threads", method: "GET", state: "complete", status: 200 },
    { route: "other", method: "OTHER", state: "pending", status: undefined },
    {
      route: "staff.proposal_decision",
      method: "POST",
      state: "failed",
      status: undefined,
    },
  ]);
  expect(
    snapshot.requests.every(
      ({ atMs, durationMs }) =>
        atMs >= 0 && (durationMs === undefined || durationMs >= 0),
    ),
  ).toBe(true);
  expect(JSON.stringify(snapshot)).not.toMatch(/private-canary|secret|raw/);
  await summary.attach(testInfo);
  const requestAttachment = testInfo.attachments.find(
    ({ name }) => name === "staff-request-summary.json",
  );
  expect(requestAttachment?.body?.toString()).not.toMatch(
    /private-canary|secret|raw/,
  );
  expect(() =>
    callbacks.get("request")?.({
      url: () => {
        throw Error("diagnostic-canary");
      },
    } as unknown as Request),
  ).not.toThrow();

  await page.setContent(
    '<main id="drawer"><button aria-label="private-canary" aria-hidden="private-canary">text-canary</button></main>',
  );
  await attachSidebarFocusState(
    testInfo,
    page.locator("#drawer"),
    "focus-state.json",
  );
  const attachment = testInfo.attachments.find(
    ({ name }) => name === "focus-state.json",
  );
  const focus = JSON.parse(attachment?.body?.toString() ?? "{}");
  expect(focus.candidates[0]).toMatchObject({ ariaHidden: false, label: null });
  expect(JSON.stringify(focus)).not.toMatch(/private-canary|text-canary/);

  const partial = new Set<string>();
  const brokenPage = {
    on(event: string) {
      if (event === "response") throw Error("diagnostic-canary");
      partial.add(event);
    },
    off(event: string) {
      partial.delete(event);
    },
  } as unknown as Page;
  expect(() => summary.watch(brokenPage)).not.toThrow();
  expect(partial.size).toBe(0);
  await expect(
    summary.attach({
      attach: async () => {
        throw Error("attachment-canary");
      },
    } as unknown as TestInfo),
  ).resolves.toBeUndefined();
  expect(() => summary.dispose()).not.toThrow();
  const teardown = new FailureRequestSummary();
  teardown.watch({
    on() {},
    off() {
      throw Error("teardown-canary");
    },
  } as unknown as Page);
  expect(() => teardown.dispose()).not.toThrow();
});

for (const hiddenCandidate of [
  "css-visibility",
  "ancestor-aria-hidden",
  "inert",
] as const) {
  for (const hiddenPosition of ["before-first", "after-last"] as const) {
    test(`mobile focus wrap skips ${hiddenCandidate} candidate ${hiddenPosition}`, async ({
      page,
      request,
    }, testInfo) => {
      await seedBarbThread(request, `Focus wrap ${hiddenCandidate}`);
      await page.setViewportSize({ width: 390, height: 844 });
      const restored = page.waitForResponse(isBarbThreadList);
      await page.goto("/staff");
      const trigger = page.getByRole("button", { name: "Ask Barb" });
      await trigger.focus();
      await page.keyboard.press("Enter");
      const drawer = page.getByTestId("barb-sidebar");
      await expect(drawer).toBeVisible();
      const restoreResponse = await restored;
      expect(restoreResponse.ok()).toBe(true);
      await expect(
        drawer.getByRole("region", {
          name: `Conversation thread with Focus wrap ${hiddenCandidate}`,
        }),
      ).toBeVisible();
      await expect(
        drawer.getByRole("button", { name: "New chat" }),
      ).toBeVisible();
      const focusable = drawer
        .locator(SIDEBAR_FOCUSABLE)
        .filter({ visible: true });
      const initialFirst = focusable.first();
      const initialLast = focusable.last();
      await initialFirst.evaluate((element) =>
        element.setAttribute("data-focus-wrap-first", "true"),
      );
      await initialLast.evaluate((element) =>
        element.setAttribute("data-focus-wrap-target", "true"),
      );
      const first = drawer.locator('[data-focus-wrap-first="true"]');
      const target = drawer.locator('[data-focus-wrap-target="true"]');
      await target.focus();
      await expect(target).toBeFocused();
      await drawer.evaluate(
        (panel, { kind, position }) => {
          const parent = document.createElement("div");
          if (kind === "css-visibility") parent.style.visibility = "hidden";
          if (kind === "ancestor-aria-hidden")
            parent.setAttribute("aria-hidden", "true");
          if (kind === "inert") parent.inert = true;
          const button = document.createElement("button");
          button.dataset.focusDiagnostic = kind;
          parent.append(button);
          if (position === "before-first") {
            const first = panel.querySelector('[data-focus-wrap-first="true"]');
            if (!first) throw new Error("visible focus boundary missing");
            first.before(parent);
          } else panel.append(parent);
        },
        { kind: hiddenCandidate, position: hiddenPosition },
      );
      const syntheticCandidate = drawer.locator(
        `[data-focus-diagnostic="${hiddenCandidate}"]`,
      );
      await expect(syntheticCandidate).toHaveCount(1);

      await first.focus();
      await expect(first).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      let shiftWrapFocused = true;
      try {
        await expectFocusWithState(
          testInfo,
          target,
          drawer,
          `${hiddenCandidate}-shift-tab-state.json`,
        );
      } catch {
        shiftWrapFocused = false;
      }

      await target.focus();
      await expect(target).toBeFocused();
      await page.keyboard.press("Tab");
      let tabWrapFocused = true;
      try {
        await expectFocusWithState(
          testInfo,
          first,
          drawer,
          `${hiddenCandidate}-tab-state.json`,
        );
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

test("a delayed passive thread restore does not steal focus after drawer interaction", async ({
  page,
  request,
}) => {
  const title = "Delayed restore focus fixture";
  await seedBarbThread(request, title);
  await page.setViewportSize({ width: 390, height: 844 });
  const release = deferred();
  const intercepted = deferred();
  const heldList: (route: Route) => Promise<void> = async (route) => {
    intercepted.release();
    await release.promise;
    await route.fallback();
  };
  await page.route(BARB_THREAD_LIST, heldList);
  const restored = page.waitForResponse(isBarbThreadList);

  try {
    await page.goto("/staff");
    await page.getByTestId("ask-barb-toggle").click();
    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    await intercepted.promise;
    const projects = drawer.getByRole("button", { name: "Open Projects" });
    await projects.focus();
    await expect(projects).toBeFocused();

    release.release();
    expect((await restored).ok()).toBe(true);
    await expect(
      drawer.getByRole("region", {
        name: `Conversation thread with ${title}`,
      }),
    ).toBeVisible();
    await expect(projects).toBeFocused();
  } finally {
    release.release();
    await page.unroute(BARB_THREAD_LIST, heldList);
  }
});

test("an explicit New chat switch focuses the new composer", async ({
  page,
  request,
}) => {
  const title = "Explicit switch focus fixture";
  await seedBarbThread(request, title);
  await page.setViewportSize({ width: 390, height: 844 });
  const restored = page.waitForResponse(isBarbThreadList);
  await page.goto("/staff");
  await page.getByTestId("ask-barb-toggle").click();
  const drawer = page.getByTestId("barb-sidebar");
  expect((await restored).ok()).toBe(true);
  await expect(
    drawer.getByRole("region", {
      name: `Conversation thread with ${title}`,
    }),
  ).toBeVisible();

  await drawer.getByRole("button", { name: "New chat" }).click();
  await expect(
    drawer.getByRole("region", {
      name: "Conversation thread with Conversation with Barb",
    }),
  ).toBeVisible();
  await expect(drawer.getByPlaceholder("Message Barb...")).toBeFocused();
});
