/**
 * Tests for the #3498 mobile chat shell 12px minimum text floor.
 *
 * #2989 set a 12px minimum text floor on Queue and Fleet (indexCss) after
 * Disco Stu's UX audit found sub-12px body/meta text. StaffConsole's mobile
 * chat-first shell (mobile.css) predates that sweep and was never covered:
 * the status badge, unread badge, run-card footer meta and project-starter
 * hint line all still render at 11px.
 *
 * CSS-content contracts (DbC), matching the pattern in
 * `mobileNavigationLayout.test.tsx` and
 * `src/__tests__/queueFleetTextSizeContrast.test.ts`: assert the rules a
 * stylesheet must contain rather than rendering a full page.
 */

import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const mobileCss = fs.readFileSync(path.join(__dirname, "../mobile.css"), "utf8");

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = mobileCss.match(new RegExp(`${escaped}\\s*\\{[^}]*\\}`));
  expect(match, `expected a rule for ${selector}`).toBeTruthy();
  return match![0];
}

describe("Mobile chat shell 12px minimum text (#3498)", () => {
  it.each([
    [".staff-mobile__status-badge", "thread status badge in the mobile header"],
    [".staff-mobile__unread-badge", "roster unread-count badge"],
    [".staff-mobile__run-footer", "mobile run-card footer meta row"],
    [".staff-mobile__project-starter span + span", "chat-first home project-starter hint line"],
  ])("%s is at least 12px (%s)", (selector) => {
    expect(rule(selector)).toMatch(/font-size:\s*(1[2-9]|[2-9][0-9]|[1-9][0-9]{2,})px/);
  });
});
