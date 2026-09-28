/**
 * rosterContextCss.test.ts — CSS contract verification for roster.css and context.css.
 * Workstream C (Issue #1721, Epic #1718).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const rosterCss = readFileSync(path.join(__dirname, "../roster.css"), "utf8");
const contextCss = readFileSync(path.join(__dirname, "../context.css"), "utf8");

describe("roster.css contract (Workstream C)", () => {
  it("does not use backdrop-filter (no glassmorphism)", () => {
    expect(rosterCss).not.toContain("backdrop-filter");
  });

  it("does not repaint the entire sidebar background on hover", () => {
    expect(rosterCss).not.toMatch(/\.staff-roster-sidebar:hover\s*\{[^}]*background/);
  });

  it("enforces ~40px row height with compact density and 24px avatar", () => {
    expect(rosterCss).toContain("min-height: 40px");
    expect(rosterCss).toContain("width: 24px");
    expect(rosterCss).toContain("height: 24px");
    expect(rosterCss).toContain("border-radius: var(--radius-sm, 6px)");
  });

  it("places status dot on the corner with 8px dimensions", () => {
    expect(rosterCss).toContain("bottom: -2px");
    expect(rosterCss).toContain("right: -2px");
    expect(rosterCss).toContain("width: 8px");
    expect(rosterCss).toContain("height: 8px");
  });

  it("enforces 2px accent focus ring on interactive elements", () => {
    expect(rosterCss).toContain("outline: 2px solid var(--accent-blue)");
  });

  it("honours prefers-reduced-motion", () => {
    expect(rosterCss).toContain("@media (prefers-reduced-motion: reduce)");
  });
});

describe("context.css contract (Workstream C)", () => {
  it("does not use backdrop-filter (no glassmorphism)", () => {
    expect(contextCss).not.toContain("backdrop-filter");
  });

  it("uses 1px hairline borders and flat opaque surfaces", () => {
    expect(contextCss).toContain("border-left: 1px solid var(--border)");
    expect(contextCss).toContain("background: var(--bg-secondary)");
  });

  it("defines thin 4px budget progress bar", () => {
    expect(contextCss).toContain("height: 4px");
  });

  it("uses 11px uppercase muted labels for sections", () => {
    expect(contextCss).toContain("font-size: 11px");
    expect(contextCss).toContain("text-transform: uppercase");
  });

  it("honours prefers-reduced-motion", () => {
    expect(contextCss).toContain("@media (prefers-reduced-motion: reduce)");
  });
});
