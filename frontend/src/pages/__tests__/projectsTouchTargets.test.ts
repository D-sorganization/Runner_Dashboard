/**
 * projectsTouchTargets.test.ts — CSS contract for the Projects page (#3748).
 * `docs/mobile-design-system.md` requires a 44px minimum touch target and
 * 12px minimum body text at the 390px mobile viewport. ProjectsPage.css is
 * the page-scoped override point: per-card buttons (via the shared `.btn`
 * primitive) and badges (via the shared `Badge` primitive) must meet both
 * minimums without changing the global `.btn`/`.badge` rules other,
 * out-of-scope pages also use.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../ProjectsPage.css"), "utf8");

/** Body of the `@media (max-width: 720px)` block (brace-balanced). */
function mobileBlock(): string {
  const start = css.indexOf("@media (max-width: 720px)");
  expect(start, "ProjectsPage.css needs a max-width: 720px block").toBeGreaterThan(-1);
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

describe("Projects page mobile touch targets (#3748)", () => {
  it("every button on the Projects page is at least 44x44 under 720px", () => {
    const body = rule(mobileBlock(), ".projects-page button");
    expect(body).toMatch(/min-height:\s*44px/);
    expect(body).toMatch(/min-width:\s*44px/);
  });

  it("badges on the Projects page are at least 12px, not the 10.5/11.5px defaults", () => {
    const start = css.indexOf(".projects-page .badge");
    expect(start, ".projects-page .badge rule").toBeGreaterThan(-1);
    const end = css.indexOf("}", start);
    const body = css.slice(start, end);
    expect(body).toMatch(/font-size:\s*(1[2-9]|[2-9]\d)px/);
  });
});
