/**
 * Geometry regression for the collapsed-sidebar icon rail (issue #3494).
 *
 * Bug: in the rail-collapsed header, the product mark (22px) + gap (8px) +
 * collapse/expand toggle (28px) need 58px but the 56px-wide rail only has
 * ~55px of content width after its border, so `justifyContent: "center"`
 * overflows both edges — the product mark clips on the left and the toggle
 * clips/overlaps the main content on the right.
 *
 * This runs against the desktop Chromium project only (the rail is a
 * desktop-shell feature; MobileShell has no rail, see the `md` breakpoint
 * skip below) at the widths named in #3494's acceptance criteria.
 */
import { test, expect, type Page } from "@playwright/test";

test.skip(
  ({ viewport }) => !viewport || viewport.width <= 768,
  "desktop rail only mounts above the md (768px) breakpoint",
);

const COLLAPSE_TOGGLE = 'button[aria-label="Collapse sidebar"], button[aria-label="Expand sidebar"]';
const NAV = 'nav[aria-label="Dashboard sections"]';

async function collapseRail(page: Page): Promise<void> {
  await page.waitForSelector(COLLAPSE_TOGGLE);
  const label = await page.getAttribute(COLLAPSE_TOGGLE, "aria-label");
  if (label === "Collapse sidebar") {
    await page.click(COLLAPSE_TOGGLE);
  }
  // Width transition is 140ms; settle before measuring.
  await page.waitForTimeout(250);
}

async function railBoxes(page: Page) {
  const nav = page.locator(NAV);
  const navBox = await nav.boundingBox();
  expect(navBox).not.toBeNull();
  const headerChildren = page.locator(`${NAV} > div:first-child > *`);
  const children = await headerChildren.all();
  const boxes: { tag: string; box: { x: number; y: number; width: number; height: number } }[] = [];
  for (const el of children) {
    const box = await el.boundingBox();
    expect(box).not.toBeNull();
    const tag = await el.evaluate((e) => e.tagName.toLowerCase());
    boxes.push({ tag, box: box! });
  }
  return { navBox: navBox!, boxes };
}

for (const width of [958, 1024, 1440]) {
  test(`collapsed rail header has no clipping/overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    await collapseRail(page);

    const { navBox, boxes } = await railBoxes(page);
    for (const { tag, box } of boxes) {
      expect(box.x, `${tag} left edge clips the rail at ${width}px`).toBeGreaterThanOrEqual(navBox.x);
      expect(
        box.x + box.width,
        `${tag} right edge overflows the rail into the main content at ${width}px`,
      ).toBeLessThanOrEqual(navBox.x + navBox.width + 0.5);
    }
  });
}

test("collapsed rail header has no clipping/overflow after repeated collapse/expand", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 });
  await page.goto("/");
  await page.waitForSelector(COLLAPSE_TOGGLE);
  for (let i = 0; i < 5; i++) {
    await page.click(COLLAPSE_TOGGLE);
    await page.waitForTimeout(150);
  }
  await collapseRail(page);

  const { navBox, boxes } = await railBoxes(page);
  for (const { tag, box } of boxes) {
    expect(box.x, `${tag} left edge clips after repeated toggling`).toBeGreaterThanOrEqual(navBox.x);
    expect(
      box.x + box.width,
      `${tag} right edge overflows after repeated toggling`,
    ).toBeLessThanOrEqual(navBox.x + navBox.width + 0.5);
  }
});
