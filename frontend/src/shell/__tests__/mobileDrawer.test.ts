/**
 * The phone "More" drawer rows must breathe: the icon sat flush against the
 * edge and touched its label, and the current page had no highlight (#1718).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../../index.css"), "utf8");

function rule(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} rule exists`).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start));
}

describe("mobile More drawer (#1718)", () => {
  it("separates the icon from its label and insets the row", () => {
    const item = rule(".mobile-shell__drawer-item");
    expect(item).toMatch(/gap:\s*var\(--space-6\)/);
    expect(item).toMatch(/padding:\s*var\(--space-6\) var\(--space-8\)/);
  });

  it("highlights the current page", () => {
    expect(rule(".mobile-shell__drawer-item--active")).toMatch(/color:\s*var\(--accent-blue\)/);
  });
});
