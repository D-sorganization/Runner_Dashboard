/**
 * Tests for the #2989 text-size/contrast fix on Queue and Fleet.
 *
 * Disco Stu's 2026-10-26 UX audit found body/meta text under 12px and
 * axe-core color-contrast violations on Queue and Fleet (the status pill's
 * `opacity: .85` pushed it to ~3.7:1). Everything here is fixed via
 * `frontend/src/index.css` overrides rather than editing
 * `pages/Fleet/{KpiHeader,RunnerCard,StatusPill}.tsx`, since #2826 is
 * mid-flight against those same files (see the comment in index.css).
 *
 * These are CSS-content contracts (DbC), matching the pattern in
 * `pages/StaffConsole/__tests__/mobileNavigationLayout.test.tsx`: assert the
 * rules a stylesheet must contain rather than rendering a full page.
 */

import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const indexCss = fs.readFileSync(
  path.join(__dirname, "../index.css"),
  "utf8",
);

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = indexCss.match(new RegExp(`${escaped}\\s*\\{[^}]*\\}`));
  expect(match, `expected a rule for ${selector}`).toBeTruthy();
  return match![0];
}

describe("Queue/Fleet minimum 12px text (#2989)", () => {
  it.each([
    [".stat-label", "Stat.tsx label, shared by Queue and Fleet KPI cards"],
    [".stat-sub", "Stat.tsx sub-line, shared by Queue and Fleet KPI cards"],
    [".mobile-kpi-label", "Queue mobile KPI strip label"],
    [".mobile-run-meta", "Queue mobile run-card meta row"],
    [".sort-indicator", "sortable table-header arrow glyph (Queue tables)"],
  ])("%s is at least 12px (%s)", (selector) => {
    expect(rule(selector)).toMatch(/font-size:\s*(1[2-9]|[2-9][0-9]|[1-9][0-9]{2,})px/);
  });

  it.each([
    [".kpi-label", "Fleet/KpiHeader.tsx inline 11px label"],
    [".mini-bar-label", "Fleet/RunnerCard.tsx inline 10px CPU/RAM label"],
    [".runner-actions .touch-button", "Fleet/RunnerCard.tsx inline 11px drain/stop/restart buttons"],
  ])("%s overrides the inline style to >= 12px (%s)", (selector) => {
    expect(rule(selector)).toMatch(/font-size:\s*12px\s*!important/);
  });
});

describe("Fleet status pill contrast (#2989)", () => {
  it("removes the opacity dimming that pushed status-pill-label below 4.5:1", () => {
    expect(rule(".status-pill-label")).toMatch(/opacity:\s*1\s*!important/);
  });
});
