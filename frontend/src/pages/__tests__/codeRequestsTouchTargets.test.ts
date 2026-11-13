/**
 * codeRequestsTouchTargets.test.ts — CSS/text-size contract for the Code
 * Requests page (#3748). `.action-btn` and the standards-toggle buttons had
 * no touch-target CSS at all (relying on default <button> sizing), and
 * several labels/meta lines used an 11px inline font size below the 12px
 * mobile-design-system.md body-text minimum.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../CodeRequests.css"), "utf8");

function mobileBlock(): string {
  const start = css.indexOf("@media (max-width: 720px)");
  expect(start, "CodeRequests.css needs a max-width: 720px block").toBeGreaterThan(-1);
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

describe("Code Requests page mobile touch targets (#3748)", () => {
  it("every button on the Code Requests page is at least 44x44 under 720px", () => {
    const body = rule(mobileBlock(), ".code-requests-page button");
    expect(body).toMatch(/min-height:\s*44px/);
    expect(body).toMatch(/min-width:\s*44px/);
  });

  it("no body text on the Code Requests page uses an 11px (or smaller) inline font size", () => {
    for (const file of ["../CodeRequests.tsx", "../CodeRequestsHistory.tsx", "../CodeRequestsPromptNotes.tsx"]) {
      const src = readFileSync(path.join(__dirname, file), "utf8");
      expect(src, file).not.toMatch(/fontSize:\s*(9|10|11)\b/);
    }
  });
});
