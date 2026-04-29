export const MOBILE_VIEWPORT_PROFILE_NAMES = [
  "iphone-12",
  "pixel-5",
  "epic-compact-375",
  "epic-standard-412",
];

function asLocator(page, target) {
  return typeof target === "string" ? page.locator(target) : target;
}

export async function tap(page, target, options = {}) {
  const locator = asLocator(page, target);
  await locator.tap({ timeout: 5000, ...options });
}

export async function longPress(page, target, options = {}) {
  const durationMs = options.durationMs ?? 600;
  const locator = asLocator(page, target);
  const box = await locator.boundingBox();
  if (!box) {
    throw new Error("Cannot long-press a hidden or detached target");
  }
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.waitForTimeout(durationMs);
  await page.mouse.up();
}

export async function swipe(page, start, end, options = {}) {
  const steps = options.steps ?? 8;
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  for (let step = 1; step <= steps; step += 1) {
    const x = start.x + ((end.x - start.x) * step) / steps;
    const y = start.y + ((end.y - start.y) * step) / steps;
    await page.mouse.move(x, y);
  }
  await page.mouse.up();
}
