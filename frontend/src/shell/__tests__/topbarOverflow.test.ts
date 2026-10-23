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

function mediaRule(query: string, selector: string): string {
  const mediaStart = css.indexOf(`@media ${query} {`);
  expect(mediaStart, `@media ${query} exists`).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", mediaStart);
  let depth = 1;
  let close = open + 1;
  while (depth > 0 && close < css.length) {
    if (css[close] === "{") depth += 1;
    else if (css[close] === "}") depth -= 1;
    close += 1;
  }
  const body = css.slice(open + 1, close - 1);
  const selectorStart = body.indexOf(`${selector} {`);
  expect(selectorStart, `${selector} override exists in ${query}`).toBeGreaterThanOrEqual(0);
  return body.slice(selectorStart, body.indexOf("}", selectorStart));
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

  it("lets a wrapped tablet action row expand the top bar vertically", () => {
    const tabletHeader = mediaRule("(min-width: 901px) and (max-width: 1100px)", ".desktop-shell__topbar");
    expect(tabletHeader).toMatch(/height:\s*auto/);
    expect(tabletHeader).toMatch(/min-height:\s*52px/);
    expect(tabletHeader).toMatch(/flex:\s*0 0 auto/);
  });
});
