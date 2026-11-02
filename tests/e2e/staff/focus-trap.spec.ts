import { expect, test } from "./fixtures";
import {
  expectFocusWithState,
  SIDEBAR_FOCUSABLE,
} from "./focusTrapDiagnostics";

for (const hiddenCandidate of [
  "css-visibility",
  "ancestor-aria-hidden",
  "inert",
] as const) {
  test(`mobile focus wrap skips ${hiddenCandidate} candidates in both directions`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/staff");
    const trigger = page.getByRole("button", { name: "Ask Barb" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    const focusable = drawer
      .locator(SIDEBAR_FOCUSABLE)
      .filter({ visible: true });
    const first = focusable.first();
    const initialLast = focusable.last();
    await initialLast.evaluate((element) =>
      element.setAttribute("data-focus-wrap-target", "true"),
    );
    const target = drawer.locator('[data-focus-wrap-target="true"]');
    await target.focus();
    await expect(target).toBeFocused();
    await drawer.evaluate((panel, kind) => {
      const parent = document.createElement("div");
      if (kind === "css-visibility") parent.style.visibility = "hidden";
      if (kind === "ancestor-aria-hidden")
        parent.setAttribute("aria-hidden", "true");
      if (kind === "inert") parent.inert = true;
      const button = document.createElement("button");
      button.dataset.focusDiagnostic = kind;
      parent.append(button);
      panel.append(parent);
    }, hiddenCandidate);
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
