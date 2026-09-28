/**
 * The desktop top bar must not push the page wider than the window (#1713):
 * at 800px its fixed-size actions made the whole page scroll sideways.
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

describe("desktop top bar (#1713)", () => {
  it("lets the actions shrink and wrap instead of overflowing", () => {
    const actions = rule(".desktop-shell__actions");
    expect(actions).toMatch(/flex:\s*0 1 auto/);
    expect(actions).toMatch(/flex-wrap:\s*wrap/);
    expect(actions).toMatch(/min-width:\s*0/);
  });

  it("keeps the top bar itself within the window", () => {
    expect(rule(".desktop-shell__topbar")).toMatch(/min-width:\s*0/);
  });
});
