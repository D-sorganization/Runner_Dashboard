import { expect, test } from "./fixtures";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const HIDDEN_KINDS = ["css-visibility", "aria-hidden", "inert"] as const;
const EDGES = ["before-first", "after-last"] as const;

for (const kind of HIDDEN_KINDS) {
  for (const edge of EDGES) {
    test(`mobile focus wrap skips ${kind} candidate at ${edge}`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/staff");
      await page.getByRole("button", { name: "Ask Barb" }).click();
      const drawer = page.getByTestId("barb-sidebar");
      await expect(drawer).toBeVisible();
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
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/staff");
  await page.getByRole("button", { name: "Ask Barb" }).click();
  const drawer = page.getByTestId("barb-sidebar");
  await expect(drawer).toBeVisible();

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
  const restored = {
    id: "thr_focus_restore",
    title: "Synthetic restored conversation",
    kind: "auto",
    participants: ["user:synthetic", "barb"],
    status: "active",
  };
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

  await page.route("**/api/v1/staff/threads**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "GET" && path.endsWith("/threads") && url.searchParams.get("role") === "barb") {
      signalListStarted();
      await listGate;
      await route.fulfill({ json: { threads: [restored] } });
    } else if (request.method() === "GET" && path.endsWith(`/threads/${restored.id}/stream`)) {
      await route.fulfill({ status: 204 });
    } else if (request.method() === "GET" && path.endsWith(`/threads/${restored.id}`)) {
      await route.fulfill({ json: { thread: restored, messages: [] } });
    } else if (request.method() === "POST" && path.endsWith("/threads")) {
      await route.fulfill({ json: explicit });
    } else {
      await route.fallback();
    }
  });

  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/staff");
    await page.getByRole("button", { name: "Ask Barb" }).click();
    await listStarted;
    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    const retainedFocus = drawer.getByRole("button", { name: "Open Projects" });
    await page.keyboard.press("Tab");
    await expect(retainedFocus).toBeFocused();

    releaseList();
    await expect(drawer.getByText(restored.title)).toBeVisible();
    await expect(retainedFocus).toBeFocused();

    await drawer.getByRole("button", { name: "New chat" }).click();
    await expect(drawer.getByText(explicit.title)).toBeVisible();
    await expect(drawer.getByRole("textbox", { name: "Staff conversation input" })).toBeFocused();
  } finally {
    releaseList();
  }
});

test("captures sanitized drawer screenshots at desktop and phone sizes", async ({ page }, testInfo) => {
  await page.route("**/api/v1/staff/threads**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname.endsWith("/threads") && url.searchParams.get("role") === "barb") {
      await route.fulfill({ json: { threads: [] } });
    } else {
      await route.fallback();
    }
  });
  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/staff");
    await page.getByRole("button", { name: "Ask Barb" }).click();
    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    const screenshot = await page.screenshot({ animations: "disabled" });
    await testInfo.attach(`drawer-${viewport.width}`, {
      body: screenshot,
      contentType: "image/png",
    });
  }
});
