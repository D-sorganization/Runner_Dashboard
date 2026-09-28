/**
 * The three-pane console must not crush the conversation column on narrower
 * windows (#1712 follow-up): at 800px wide it rendered one word per line.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../desktop.css"), "utf8");

describe("desktop console responsive layout", () => {
  it("overlays the context pane on the conversation's right edge on medium widths (#1718)", () => {
    // Dropping it into a second grid row squeezed it under the roster and scrolled the page sideways.
    const block = css.split("@media (max-width: 1280px)")[1] ?? "";
    expect(block).toMatch(/\.staff-console,\s*\.staff-console--no-context\s*\{[^}]*grid-template-columns:\s*minmax\(200px, 260px\) minmax\(0, 1fr\)/);
    expect(block).toMatch(/\.staff-console__context\s*\{[^}]*position:\s*absolute/);
    expect(block).not.toMatch(/\.staff-console__context\s*\{[^}]*grid-column:\s*1 \/ -1/);
    expect(css).toMatch(/\.staff-console\s*\{[^}]*position:\s*relative/);
  });

  it("shows the overlay's own close button only on medium widths (#1718)", () => {
    // The overlay covers the header toggle. The base rule sits after the media block,
    // so the medium-width rule needs the higher specificity to win.
    expect(css).toMatch(/\n\.staff-console__context-close\s*\{[^}]*display:\s*none/);
    const block = css.split("@media (max-width: 1280px)")[1]?.split("@media")[0] ?? "";
    expect(block).toMatch(/\.staff-console__context \.staff-console__context-close\s*\{[^}]*display:\s*inline-flex/);
  });

  it("stacks every pane in one column on narrow widths", () => {
    const block = css.split("@media (max-width: 900px)")[1] ?? "";
    expect(block).toMatch(/\.staff-console,\s*\.staff-console--no-context\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  });

  it("enforces app-height layout with bounded height and internally scrolling columns", () => {
    expect(css).toMatch(/\.staff-console\s*\{[^}]*height:\s*calc\(100dvh[^;]+\)/);
    expect(css).toMatch(/\.staff-console\s*\{[^}]*min-height:\s*520px/);

    expect(css).toMatch(/\.staff-console__roster\s*\{[^}]*min-height:\s*0/);
    expect(css).toMatch(/\.staff-console__roster\s*\{[^}]*overflow-y:\s*auto/);

    expect(css).toMatch(/\.staff-console__main\s*\{[^}]*min-height:\s*0/);
    expect(css).toMatch(/\.staff-console__main\s*\{[^}]*overflow:\s*hidden/);

    expect(css).toMatch(/\.staff-console__context\s*\{[^}]*min-height:\s*0/);
    expect(css).toMatch(/\.staff-console__context\s*\{[^}]*overflow-y:\s*auto/);
  });
});

