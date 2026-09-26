import { describe, expect, it } from "vitest";
import {
  buildBulkIssueRequest,
  buildBulkPRRequest,
  formatBulkResponseResult,
} from "../remediationBulkRequest";
import type { StaffRequestResponse } from "../../Staff/staffApi";

describe("remediationBulkRequest", () => {
  describe("buildBulkIssueRequest", () => {
    it("builds an issue.act request with bulk target numbers and options", () => {
      const items = [
        { repo: "Tools", number: 101 },
        { repo: "Tools", number: 102 },
      ];
      const req = buildBulkIssueRequest(items, {
        provider: "claude",
        prompt: "Fix bugs",
        force: true,
        approved_by: "dieter",
      });

      expect(req).toEqual({
        kind: "issue.act",
        target: {
          repo: "Tools",
          issues: [101, 102],
          ref: "",
        },
        provider: "claude",
        prompt: "Fix bugs",
        force: true,
        approved_by: "dieter",
        dry_run: false,
        machine: "local",
      });
    });

    it("falls back to repository / full_name and ignores invalid numbers", () => {
      const items = [
        { repository: "D-sorganization/Runner_Dashboard", pr_number: 45 },
        { full_name: "D-sorganization/Runner_Dashboard", number: 0 },
      ];
      const req = buildBulkIssueRequest(items);
      expect(req.target?.repo).toBe("D-sorganization/Runner_Dashboard");
      expect(req.target?.issues).toEqual([45]);
      expect(req.force).toBe(false);
      expect(req.approved_by).toBe("anonymous");
      expect(req.dry_run).toBe(false);
      expect(req.machine).toBe("local");
    });
  });

  describe("buildBulkPRRequest", () => {
    it("builds a pr.act request with bulk PR numbers and options", () => {
      const items = [
        { repo: "Runner_Dashboard", number: 201 },
        { repo: "Runner_Dashboard", pr_number: 202 },
      ];
      const req = buildBulkPRRequest(items, {
        provider: "jules_api",
        prompt: "Review PRs",
        approved_by: "alice",
      });

      expect(req).toEqual({
        kind: "pr.act",
        target: {
          repo: "Runner_Dashboard",
          prs: [201, 202],
          ref: "",
        },
        provider: "jules_api",
        prompt: "Review PRs",
        force: false,
        approved_by: "alice",
        dry_run: false,
        machine: "local",
      });
    });
  });

  describe("formatBulkResponseResult", () => {
    it("formats completely successful execution", () => {
      const resp: StaffRequestResponse = {
        state: "executed",
        kind: "issue.act",
        action: "issue.act",
        result: {
          status: "dispatched",
          accepted: 3,
          dispatched: [{ number: 1 }, { number: 2 }, { number: 3 }],
          rejected: [],
        },
      };

      const outcome = formatBulkResponseResult(resp, 3, "issue");
      expect(outcome.type).toBe("success");
      expect(outcome.text).toBe("Dispatched 3 issue(s) successfully.");
    });

    it("formats partial backend failure per target", () => {
      const resp: StaffRequestResponse = {
        state: "executed",
        kind: "issue.act",
        action: "issue.act",
        result: {
          status: "partial",
          accepted: 2,
          dispatched: [{ number: 10 }, { number: 11 }],
          rejected: [{ number: 12, error: "Issue does not exist" }],
        },
      };

      const outcome = formatBulkResponseResult(resp, 3, "issue");
      expect(outcome.type).toBe("error");
      expect(outcome.text).toContain("Dispatched 2 of 3 issue(s)");
      expect(outcome.text).toContain("#12: Issue does not exist");
    });

    it("formats complete failure when all targets are rejected", () => {
      const resp: StaffRequestResponse = {
        state: "executed",
        kind: "pr.act",
        action: "pr.act",
        result: {
          status: "failed",
          accepted: 0,
          dispatched: [],
          rejected: [
            { number: 101, error: "conflict" },
            { number: 102, error: "closed" },
          ],
        },
      };

      const outcome = formatBulkResponseResult(resp, 2, "PR");
      expect(outcome.type).toBe("error");
      expect(outcome.text).toContain("Dispatch failed for all 2 PR(s)");
      expect(outcome.text).toContain("#101: conflict");
      expect(outcome.text).toContain("#102: closed");
    });

    it("formats approval_required state", () => {
      const resp: StaffRequestResponse = {
        state: "approval_required",
        kind: "issue.act",
        action: "issue.act",
        approval: "Risk level medium requires approval",
      };

      const outcome = formatBulkResponseResult(resp, 2, "issue");
      expect(outcome.type).toBe("success");
      expect(outcome.text).toContain("awaiting approval");
      expect(outcome.text).toContain("Risk level medium requires approval");
    });
  });
});
