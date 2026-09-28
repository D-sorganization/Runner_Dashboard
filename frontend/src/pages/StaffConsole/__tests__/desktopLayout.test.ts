/**
 * The three-pane console must not crush the conversation column on narrower
 * windows (#1712 follow-up): at 800px wide it rendered one word per line.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../desktop.css"), "utf8");

describe("desktop console responsive layout", () => {
  it("moves the context pane under the conversation on medium widths", () => {
    const block = css.split("@media (max-width: 1280px)")[1] ?? "";
    expect(block).toMatch(/\.staff-console,\s*\.staff-console--no-context\s*\{[^}]*grid-template-columns:\s*minmax\(200px, 260px\) minmax\(0, 1fr\)/);
    expect(block).toMatch(/\.staff-console__context\s*\{[^}]*grid-column:\s*1 \/ -1/);
  });

  it("stacks every pane in one column on narrow widths", () => {
    const block = css.split("@media (max-width: 900px)")[1] ?? "";
    expect(block).toMatch(/\.staff-console,\s*\.staff-console--no-context\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  });
});
