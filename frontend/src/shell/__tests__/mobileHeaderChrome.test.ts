/**
 * mobileHeaderChrome.test.ts — CSS contract for the mobile shell header's
 * single Barb entry point (#2987).
 *
 * Disco Stu's 2026-10-26 UX audit found the mobile `/staff` header's "Ask
 * Barb" button under the WCAG 2.2 44x44 minimum target size.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../../index.css"), "utf8");

function rule(selector: string): string {
  const start = css.indexOf(`\n${selector} {`);
  const end = css.indexOf("}", start);
  expect(start, `rule for ${selector}`).toBeGreaterThan(-1);
  return css.slice(start, end);
}

describe("mobile shell header Ask Barb button (#2987)", () => {
  it("meets the 44x44 minimum hit target", () => {
    const body = rule(".mobile-shell__ask-barb");
    expect(body).toMatch(/min-height:\s*44px/);
    expect(body).toMatch(/min-width:\s*44px/);
  });
});
