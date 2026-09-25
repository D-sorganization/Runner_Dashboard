import { describe, expect, it } from "vitest";
import {
  calculateRoleStatus,
  formatRelativeAge,
  resolveOperationalTier,
} from "../statusCalculator";

describe("statusCalculator", () => {
  describe("resolveOperationalTier", () => {
    it("maps known roles to their tier", () => {
      expect(resolveOperationalTier("barb")).toBe("Leadership");
      expect(resolveOperationalTier("board")).toBe("Leadership");
      expect(resolveOperationalTier("orchestrator")).toBe("Leadership");
      expect(resolveOperationalTier("project-steward")).toBe("Project Managers");
      expect(resolveOperationalTier("librarian")).toBe("Specialists");
      expect(resolveOperationalTier("cartographer")).toBe("Specialists");
      expect(resolveOperationalTier("fleet-critic")).toBe("Specialists");
      expect(resolveOperationalTier("fleet-maintenance")).toBe("Operations");
      expect(resolveOperationalTier("night-watch")).toBe("Operations");
      expect(resolveOperationalTier("sanitation")).toBe("Operations");
    });

    it("parses raw group strings with fuzzy match", () => {
      expect(resolveOperationalTier("custom-role", "leadership")).toBe("Leadership");
      expect(resolveOperationalTier("custom-role", "Project Management")).toBe("Project Managers");
      expect(resolveOperationalTier("custom-role", "specialist")).toBe("Specialists");
      expect(resolveOperationalTier("custom-role", "ops")).toBe("Operations");
    });

    it("falls back to Specialists for unknown roles", () => {
      expect(resolveOperationalTier("unknown-scout")).toBe("Specialists");
    });
  });

  describe("calculateRoleStatus", () => {
    it("identifies invalid roles with schema errors", () => {
      const res = calculateRoleStatus({
        valid: false,
        errors: ["Invalid schema syntax in line 14"],
      });
      expect(res.status).toBe("invalid");
      expect(res.status_reason).toContain("Invalid schema syntax in line 14");
    });

    it("identifies retired roles as unavailable", () => {
      const res = calculateRoleStatus({
        valid: true,
        retired: true,
      });
      expect(res.status).toBe("unavailable");
      expect(res.status_reason).toBe("Role is retired");
    });

    it("identifies roles on hold as unavailable", () => {
      const res = calculateRoleStatus({
        valid: true,
        holds: ["manual-hold"],
      });
      expect(res.status).toBe("unavailable");
      expect(res.status_reason).toContain("manual-hold");
    });

    it("identifies roles without installed providers as unavailable", () => {
      const res = calculateRoleStatus(
        {
          valid: true,
          providers: ["codex"],
          dispatchable: true,
        },
        { claude: true, codex: false }
      );
      expect(res.status).toBe("unavailable");
      expect(res.status_reason).toContain("No provider signed in / installed");
    });

    it("identifies roles with unread messages as 'needs you'", () => {
      const res = calculateRoleStatus({
        valid: true,
        dispatchable: true,
        unread_count: 2,
        active_runs: 1,
      });
      expect(res.status).toBe("needs you");
      expect(res.status_reason).toContain("2 unread messages");
    });

    it("identifies roles with active runs as 'working'", () => {
      const res = calculateRoleStatus({
        valid: true,
        dispatchable: true,
        unread_count: 0,
        active_runs: 3,
      });
      expect(res.status).toBe("working");
      expect(res.status_reason).toContain("Working on 3 active runs");
    });

    it("identifies ready roles as 'idle'", () => {
      const res = calculateRoleStatus({
        valid: true,
        dispatchable: true,
        unread_count: 0,
        active_runs: 0,
      });
      expect(res.status).toBe("idle");
      expect(res.status_reason).toBe("Idle · Ready for assignment");
    });
  });

  describe("formatRelativeAge", () => {
    it("handles seconds ago as 'just now'", () => {
      const nowIso = new Date().toISOString();
      expect(formatRelativeAge(nowIso)).toBe("just now");
    });

    it("handles minutes ago", () => {
      const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
      expect(formatRelativeAge(fiveMinAgo)).toBe("5m ago");
    });

    it("handles hours ago", () => {
      const twoHoursAgo = new Date(Date.now() - 2 * 3600 * 1000).toISOString();
      expect(formatRelativeAge(twoHoursAgo)).toBe("2h ago");
    });

    it("handles yesterday", () => {
      const yesterday = new Date(Date.now() - 25 * 3600 * 1000).toISOString();
      expect(formatRelativeAge(yesterday)).toBe("yesterday");
    });

    it("returns empty string for invalid dates", () => {
      expect(formatRelativeAge("not-a-date")).toBe("");
    });
  });
});
