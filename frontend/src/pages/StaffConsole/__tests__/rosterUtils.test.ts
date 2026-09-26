import { describe, expect, it } from "vitest";

import { categorizeRole } from "../rosterUtils";
import type { StaffRoleItem } from "../rosterUtils";

const role = (name: string): StaffRoleItem => ({ name }) as StaffRoleItem;

describe("categorizeRole advisors (Repository_Management#1788)", () => {
  it.each(["disciple", "vision-quest", "mad-scientist"])("puts %s with the advisors", (name) => {
    expect(categorizeRole(role(name))).toBe("advisors");
  });
});
