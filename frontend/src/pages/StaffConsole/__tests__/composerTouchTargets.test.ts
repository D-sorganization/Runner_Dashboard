/**
 * composerTouchTargets.test.ts — CSS contract for mobile composer touch targets (#2988).
 * WCAG 2.2 target size: composer controls must be comfortably tappable at phone width.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../composer.css"), "utf8");

/** Body of the `@media (max-width: 720px)` block (brace-balanced). */
function mobileBlock(): string {
  const start = css.indexOf("@media (max-width: 720px)");
  expect(start, "composer.css needs a max-width: 720px block").toBeGreaterThan(-1);
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
  expect(m, `mobile rule for ${selector}`).not.toBeNull();
  return m![1];
}

describe("composer mobile touch targets (#2988)", () => {
  const block = () => mobileBlock();

  it("textarea is at least 44px tall when empty", () => {
    expect(rule(block(), ".staff-composer__textarea")).toMatch(/min-height:\s*44px/);
  });

  it("icon buttons (attach, voice, send) are at least 44x44", () => {
    for (const sel of [
      ".staff-composer__attach-btn",
      ".staff-composer__voice-btn",
      ".staff-composer__send-btn",
    ]) {
      const body = rule(block(), sel);
      expect(body, sel).toMatch(/min-width:\s*44px/);
      expect(body, sel).toMatch(/min-height:\s*44px/);
    }
  });

  it("sensitivity dropdown is at least 32px tall with 14px text", () => {
    const body = rule(block(), ".staff-composer__sensitivity");
    expect(body).toMatch(/min-height:\s*32px/);
    expect(body).toMatch(/font-size:\s*14px/);
  });

  it("attachment remove button meets the 24px minimum", () => {
    expect(rule(block(), ".staff-composer__chip-remove")).toMatch(/min-height:\s*(2[4-9]|[3-9]\d)px/);
  });
});
