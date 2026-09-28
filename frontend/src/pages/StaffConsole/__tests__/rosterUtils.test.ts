import { describe, expect, it } from "vitest";

import {
  categorizeRole,
  computeRoleStatus,
  getRoleHue,
  getRoleTooltipText,
} from "../rosterUtils";
import type { StaffRoleItem } from "../types";

const role = (name: string): StaffRoleItem =>
  ({
    name,
    title: name,
    summary: name,
    valid: true,
  }) as StaffRoleItem;

describe("categorizeRole advisors (Repository_Management#1788)", () => {
  it.each(["disciple", "vision-quest", "mad-scientist"])("puts %s with the advisors", (name) => {
    expect(categorizeRole(role(name))).toBe("advisors");
  });
});

describe("computeRoleStatus hold presentation (Workstream C / #1721)", () => {
  it("formats single hold as '1 standing rule' instead of 'held: ...'", () => {
    const heldRole: StaffRoleItem = {
      ...role("held-1"),
      holds: ["quarantine"],
    };
    const res = computeRoleStatus(heldRole);
    expect(res.status).toBe("unavailable");
    expect(res.reason).toBe("1 standing rule");
  });

  it("formats multiple holds as 'N standing rules'", () => {
    const heldRole: StaffRoleItem = {
      ...role("held-2"),
      holds: ["quarantine", "C3 HOLD", "rate-limit"],
    };
    const res = computeRoleStatus(heldRole);
    expect(res.status).toBe("unavailable");
    expect(res.reason).toBe("3 standing rules");
  });
});

describe("getRoleTooltipText (Workstream C / #1721)", () => {
  it("returns full hold reasons joined in tooltip text", () => {
    const heldRole: StaffRoleItem = {
      ...role("held-specialist"),
      holds: ["quarantine", "C3 HOLD"],
    };
    expect(getRoleTooltipText(heldRole)).toBe("quarantine, C3 HOLD");
  });

  it("returns reason for budget exhausted and provider issues", () => {
    const budgetRole: StaffRoleItem = {
      ...role("overbudget"),
      budget: { daily_limit: 10, spend_today: 10 },
    };
    expect(getRoleTooltipText(budgetRole)).toBe("budget reached");

    const noProvRole: StaffRoleItem = {
      ...role("no-prov"),
      providers: ["custom-llm"],
    };
    expect(getRoleTooltipText(noProvRole, { "custom-llm": false })).toBe("no provider signed in");
  });
});

describe("getRoleHue (Workstream C / #1721)", () => {
  it("computes a deterministic hue between 0 and 359", () => {
    const hue1 = getRoleHue("architect");
    const hue2 = getRoleHue("architect");
    expect(hue1).toBe(hue2);
    expect(hue1).toBeGreaterThanOrEqual(0);
    expect(hue1).toBeLessThan(360);

    const hue3 = getRoleHue("librarian");
    expect(hue3).toBeGreaterThanOrEqual(0);
    expect(hue3).toBeLessThan(360);
  });
});

