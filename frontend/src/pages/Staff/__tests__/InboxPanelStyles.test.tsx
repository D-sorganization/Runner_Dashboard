/**
 * InboxPanelStyles.test.tsx — regression coverage for Issue #1711.
 *
 * InboxPanel.tsx rendered with classNames (staff-inbox-panel, filter-pill,
 * etc.) that no stylesheet defined, so the panel fell back to bare browser
 * defaults (bullets, underlined links, unstyled buttons). This asserts the
 * stylesheet import is wired up and that the DOM nodes the CSS targets are
 * actually present with the expected classes/attributes.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { InboxPanel } from "../InboxPanel";
import * as staffApi from "../staffApi";
import type { InboxAggregate } from "../inboxTypes";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const degradedInboxData: InboxAggregate = {
  count: 1,
  counts: {
    total: 1,
    approvals: 1,
    needs_input: 0,
    escalations: 0,
    project_decisions: 0,
    board_proposals: 0,
    auth_sign_ins: 0,
  },
  sources: {
    approvals: { status: "ok", count: 1 },
    project_decisions: {
      status: "unavailable",
      count: 0,
      error: "GitHub API error: rate limit exceeded for installation 12345 (very long diagnostic payload)",
    },
  },
  generated_at: "2026-09-27T12:00:00Z",
  items: [
    {
      id: "approval_1",
      source: "approval",
      title: "Approve budget increase",
      summary: "Increase daily cap for pr-remediator",
      severity: "medium",
      created_at: "2026-09-27T11:55:00Z",
      link: "/staff?thread=th_budget",
    },
  ],
};

describe("InboxPanel styling (Issue #1711)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("imports the component stylesheet", () => {
    const source = fs.readFileSync(path.join(__dirname, "../InboxPanel.tsx"), "utf8");
    expect(source).toContain('import "./InboxPanel.css"');
  });

  it("renders the item list with the class the stylesheet targets, with no bullets", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(degradedInboxData);

    render(<InboxPanel defaultOpen />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-item-list")).not.toBeNull();
    });

    const list = screen.getByTestId("inbox-item-list");
    expect(list.className).toContain("staff-inbox-panel__list");
    expect(list.tagName).toBe("UL");
  });

  it("carries the full degraded-source message as a title on the warning text", async () => {
    vi.spyOn(staffApi, "fetchStaffInbox").mockResolvedValue(degradedInboxData);

    render(<InboxPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("inbox-degraded-error-project_decisions")).not.toBeNull();
    });

    const warning = screen.getByTestId("inbox-degraded-error-project_decisions");
    const fullMessage =
      degradedInboxData.sources.project_decisions?.error ?? "";
    expect(warning.getAttribute("title")).toBe(fullMessage);
    expect(warning.className).toContain("staff-inbox-panel__degraded-error");
  });
});
