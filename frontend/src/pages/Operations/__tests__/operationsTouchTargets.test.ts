/**
 * operationsTouchTargets.test.ts — CSS contract for the Operations page
 * (#3748). The section-tab buttons in OperationsStatusBanner and the various
 * per-section action buttons are plain `<button>` elements under
 * `.operations-page`; OperationsPage.css is the scoped override point so a
 * 44px minimum applies on Operations without touching Fleet/Queue/Staff.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../OperationsPage.css"), "utf8");

function mobileBlock(): string {
  const start = css.indexOf("@media (max-width: 720px)");
  expect(start, "OperationsPage.css needs a max-width: 720px block").toBeGreaterThan(-1);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(start, i + 1);
  }
  throw new Error("unbalanced media block");
}

function rule(block: string, selector: string): string {
  const start = block.indexOf(selector);
  const end = block.indexOf("}", start);
  const m = start >= 0 && end > start ? [null, block.slice(start, end)] : null;
  expect(m, `rule for ${selector}`).not.toBeNull();
  return m![1];
}

describe("Operations page mobile touch targets (#3748)", () => {
  it("every button on the Operations page is at least 44x44 under 720px", () => {
    const body = rule(mobileBlock(), ".operations-page button");
    expect(body).toMatch(/min-height:\s*44px/);
    expect(body).toMatch(/min-width:\s*44px/);
  });
});
