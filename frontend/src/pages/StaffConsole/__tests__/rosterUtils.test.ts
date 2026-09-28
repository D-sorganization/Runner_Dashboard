import { describe, expect, it } from "vitest";

import {
  categorizeRole,
  computeRoleStatus,
  formatRoleWindow,
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

describe("computeRoleStatus and standing rules (guardrail holds, #1726)", () => {
  it("a role with only standing rules (declared holds:) stays dispatchable, not unavailable", () => {
    const heldRole: StaffRoleItem = {
      ...role("held-1"),
      holds: ["quarantine"],
    };
    const res = computeRoleStatus(heldRole);
    // owner decision (#1726): seeded holds are guardrails — they never block scheduling,
    // so they must not make the role "unavailable" in the roster either.
    expect(res.status).toBe("idle");
  });

  it("multiple standing rules still leave the role dispatchable", () => {
    const heldRole: StaffRoleItem = {
      ...role("held-2"),
      holds: ["quarantine", "C3 HOLD", "rate-limit"],
    };
    const res = computeRoleStatus(heldRole);
    expect(res.status).toBe("idle");
  });

  it("a role that is actually unavailable for another reason stays unavailable even with standing rules", () => {
    const heldAndBudgeted: StaffRoleItem = {
      ...role("held-3"),
      holds: ["quarantine"],
      budget: { usd_per_day: 10, spend_today: 10 },
    };
    expect(computeRoleStatus(heldAndBudgeted).status).toBe("unavailable");
  });
});

describe("getRoleTooltipText (Workstream C / #1721, relabeled #1726)", () => {
  it("labels declared holds as standing rules, not a blocking hold", () => {
    const heldRole: StaffRoleItem = {
      ...role("held-specialist"),
      holds: ["quarantine", "C3 HOLD"],
    };
    expect(getRoleTooltipText(heldRole)).toBe("Standing rule: quarantine, C3 HOLD");
  });

  it("returns reason for budget exhausted and provider issues", () => {
    const budgetRole: StaffRoleItem = {
      ...role("overbudget"),
      budget: { usd_per_day: 10, spend_today: 10 },
    };
    expect(getRoleTooltipText(budgetRole)).toBe("budget reached");

    const noProvRole: StaffRoleItem = {
      ...role("no-prov"),
      providers: ["custom-llm"],
    };
    expect(getRoleTooltipText(noProvRole, { "custom-llm": false })).toBe("no provider signed in");
  });
});

describe("formatRoleWindow (#1744)", () => {
  it("returns an empty string for null or undefined", () => {
    expect(formatRoleWindow(null)).toBe("");
    expect(formatRoleWindow(undefined)).toBe("");
  });

  it("formats a {start, end} window with an en dash", () => {
    expect(formatRoleWindow({ start: "22:00", end: "06:00" })).toBe("22:00–06:00");
  });

  it("passes a plain string window through unchanged", () => {
    expect(formatRoleWindow("22:00-06:00")).toBe("22:00-06:00");
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

