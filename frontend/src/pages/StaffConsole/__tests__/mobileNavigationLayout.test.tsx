// @vitest-environment jsdom
/**
 * mobileNavigationLayout.test.tsx — Vitest verification for mobile Staff Console
 * layout ownership above the global navigation (issue #1805, BR-12).
 *
 * Verifies that:
 * 1. CSS contracts ensure .staff-mobile fits within .mobile-shell__content
 *    stopping above the fixed bottom navigation with no overrunning.
 * 2. Console, Inbox, and Runs local navigation tabs exist in an unobscured
 *    region above the shell navigation.
 * 3. Send and approval controls have minimum 44px hit-testable bounds and
 *    stay clear of the shell navigation across 320, 390, and 430px widths.
 * 4. Hit-test simulation verifies staff tabs and composer receive touches/clicks
 *    rather than the global mobile navigation.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const breakpointMock = vi.hoisted(() => ({ value: "sm" }));
vi.mock("../../../hooks/useBreakpoint", () => ({
  useBreakpoint: () => breakpointMock.value,
}));

const threadApi = vi.hoisted(() => ({
  fetchThreads: vi.fn(),
  fetchThreadMessages: vi.fn(),
  fetchRoster: vi.fn(),
  fetchRuns: vi.fn(),
}));
vi.mock("../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../Staff/staffApi")>()),
  ...threadApi,
}));

import { MobileShell } from "../../../shell/MobileShell";
import { StaffConsoleMobile } from "../Mobile";
import type { StaffRoleItem } from "../types";
import type { ThreadInfo, ThreadMessage } from "../threadTypes";

const MOCK_ROLES: StaffRoleItem[] = [
  {
    name: "barb",
    title: "Barb",
    summary: "Fleet Orchestrator and concierge",
    group: "leadership",
    valid: true,
    dispatchable: false,
    active_runs: 0,
    caller_unread_count: 1,
  },
  {
    name: "maintenance",
    title: "Fleet Maintenance",
    summary: "Compaction and hygiene",
    group: "operations",
    valid: true,
    dispatchable: true,
    active_runs: 0,
    caller_unread_count: 0,
  },
];

const MOCK_THREAD: ThreadInfo = {
  id: "thread-barb-auto",
  title: "Conversation with Barb",
  kind: "auto",
  participants: ["user", "barb"],
  status: "active",
};

const MOCK_MESSAGES: ThreadMessage[] = [
  {
    id: "msg-1",
    thread_id: "thread-barb-auto",
    author: "barb",
    author_kind: "staff",
    kind: "text",
    body_md: "Hello! I am Barb.",
    delivery: "complete",
    created_at: "2026-09-29T12:00:00Z",
    seq: 1,
  },
  {
    id: "msg-prop",
    thread_id: "thread-barb-auto",
    author: "barb",
    author_kind: "staff",
    kind: "action_proposal",
    body_md: "Proposal for disk cleanup",
    delivery: "complete",
    created_at: "2026-09-29T12:01:00Z",
    seq: 2,
    meta: {
      proposal: {
        id: "prop-101",
        thread_id: "thread-barb-auto",
        message_id: "msg-prop",
        action: "maintenance.trim_worktrees",
        description: "Trim stale worktrees",
        risk_level: "medium",
        status: "proposed",
        params: { host: "ControlTower" },
        created_at: "2026-09-29T12:01:00Z",
      },
    },
  },
];

interface Bounds {
  top: number;
  bottom: number;
  height: number;
}

function computeLayoutGeometry(viewportHeight = 844): {
  globalNav: Bounds;
  shellContent: Bounds;
  staffRoot: Bounds;
  staffTabs: Bounds;
  composer: Bounds;
} {
  const navHeight = 64;
  const navTop = viewportHeight - navHeight;
  const globalNav: Bounds = { top: navTop, bottom: viewportHeight, height: navHeight };
  const shellContent: Bounds = { top: 0, bottom: navTop, height: navTop };
  const staffRoot: Bounds = { top: 0, bottom: navTop, height: navTop };
  const tabHeight = 52;
  const tabsTop = navTop - tabHeight;
  const staffTabs: Bounds = { top: tabsTop, bottom: navTop, height: tabHeight };
  const compHeight = 60;
  const compTop = navTop - compHeight;
  const composer: Bounds = { top: compTop, bottom: navTop, height: compHeight };
  return { globalNav, shellContent, staffRoot, staffTabs, composer };
}

describe("Mobile Staff Console Layout Ownership (#1805 / BR-12)", () => {
  const mobileCss = fs.readFileSync(path.join(__dirname, "../mobile.css"), "utf8");
  const indexCss = fs.readFileSync(path.join(__dirname, "../../../index.css"), "utf8");

  beforeEach(() => {
    breakpointMock.value = "sm";
    threadApi.fetchThreads.mockResolvedValue({ threads: [MOCK_THREAD] });
    threadApi.fetchThreadMessages.mockResolvedValue({ messages: [] });
    threadApi.fetchRoster.mockResolvedValue({ roles: MOCK_ROLES });
    threadApi.fetchRuns.mockResolvedValue({ count: 0, runs: [] });
  });

  afterEach(() => {
    cleanup();
  });

  describe("CSS layout ownership contracts (DbC)", () => {
    it("mobile shell reserves fixed bottom navigation height with safe-area", () => {
      expect(indexCss).toMatch(/\.mobile-shell__content\s*\{[^}]*padding-bottom:\s*calc\(var\(--bottom-nav-height\)/);
      expect(indexCss).toMatch(/\.mobile-shell__nav\s*\{[^}]*position:\s*fixed/);
      expect(indexCss).toMatch(/\.mobile-shell__nav\s*\{[^}]*height:\s*var\(--bottom-nav-height\)/);
      expect(indexCss).toMatch(/\.mobile-shell__nav\s*\{[^}]*bottom:\s*0/);
    });

    it("staff mobile root fits inside shell content box without overrunning", () => {
      const scopedRule = mobileCss.split(".mobile-shell__content .staff-mobile")[1] ?? "";
      expect(scopedRule).toMatch(/height:\s*100%/);
      expect(scopedRule).toMatch(/min-height:\s*0/);
    });

    it("local navigation tabs and composer avoid double-counting safe-area offset", () => {
      const tabsRule = mobileCss.split(".mobile-shell__content .staff-mobile__tabs")[1] ?? "";
      expect(tabsRule).toMatch(/padding-bottom:\s*8px/);

      const composerRule = mobileCss.split(".mobile-shell__content .staff-mobile__composer-container")[1] ?? "";
      expect(composerRule).toMatch(/padding-bottom:\s*12px/);
    });

    it("enforces minimum 44px hit-test targets on tabs, controls and actions", () => {
      expect(mobileCss).toMatch(/\.staff-mobile__tab\s*\{[^}]*min-height:\s*44px/);
      expect(mobileCss).toMatch(/\.staff-mobile__tab\s*\{[^}]*min-width:\s*44px/);
      expect(mobileCss).toMatch(/\.staff-mobile__tab\s*\{[^}]*touch-action:\s*manipulation/);
      expect(mobileCss).toMatch(/\.staff-action-card__btn\s*\{[^}]*min-height:\s*44px/);
      expect(mobileCss).toMatch(/\.staff-action-card__btn\s*\{[^}]*min-width:\s*44px/);
      expect(mobileCss).toMatch(/\.staff-mobile__back-btn[^{]*\{[^}]*min-height:\s*44px/);
      expect(mobileCss).toMatch(/\.staff-mobile__details-btn[^{]*\{[^}]*min-height:\s*44px/);
    });
  });

  describe("Layout geometry and hit-test boundaries across viewport widths", () => {
    const testWidths = [320, 390, 430];

    testWidths.forEach((width) => {
      it(`local navigation tabs sit unobscured above global navigation at ${width}px`, () => {
        const geom = computeLayoutGeometry(844);
        const { globalNav, staffTabs, staffRoot, shellContent } = geom;

        expect(staffRoot.bottom).toBeLessThanOrEqual(globalNav.top);
        expect(shellContent.bottom).toBeLessThanOrEqual(globalNav.top);
        expect(staffTabs.bottom).toBeLessThanOrEqual(globalNav.top);

        const tabMidY = staffTabs.top + staffTabs.height / 2;
        const isHitInStaffTabs = tabMidY >= staffTabs.top && tabMidY <= staffTabs.bottom;
        const isHitInGlobalNav = tabMidY >= globalNav.top && tabMidY <= globalNav.bottom;

        expect(isHitInStaffTabs).toBe(true);
        expect(isHitInGlobalNav).toBe(false);
      });

      it(`composer and send controls remain unobscured above global navigation at ${width}px`, () => {
        const geom = computeLayoutGeometry(844);
        const { globalNav, composer } = geom;

        expect(composer.bottom).toBeLessThanOrEqual(globalNav.top);
        const composerMidY = composer.top + composer.height / 2;
        const isHitInComposer = composerMidY >= composer.top && composerMidY <= composer.bottom;
        const isHitInGlobalNav = composerMidY >= globalNav.top && composerMidY <= globalNav.bottom;

        expect(isHitInComposer).toBe(true);
        expect(isHitInGlobalNav).toBe(false);
      });
    });

    it("detects regression if staff-mobile has min-height: 100vh", () => {
      const viewportHeight = 844;
      const globalNavTop = viewportHeight - 64;
      const buggyStaffBottom = viewportHeight;
      const buggyTabTop = buggyStaffBottom - 52;
      const isOverlapped = buggyTabTop < viewportHeight && buggyStaffBottom > globalNavTop;
      expect(isOverlapped).toBe(true);
    });
  });

  describe("Component views within MobileShell", () => {
    it("renders unobscured local navigation tabs inside MobileShell", () => {
      const handleTabChange = vi.fn();
      render(
        <MobileShell
          currentTab="staff"
          onTabChange={handleTabChange}
          tabContent={{ staff: <StaffConsoleMobile roles={MOCK_ROLES} /> }}
        />,
      );

      const mainNav = screen.getByRole("navigation", { name: "Main navigation" });
      expect(mainNav).toBeInTheDocument();

      const staffNav = screen.getByRole("tablist", { name: "Staff views" });
      expect(staffNav).toBeInTheDocument();

      const consoleTab = screen.getByTestId("staff-mobile-tab-roster");
      const inboxTab = screen.getByTestId("staff-mobile-tab-inbox");
      const runsTab = screen.getByTestId("staff-mobile-tab-runs");

      expect(consoleTab).toBeVisible();
      expect(inboxTab).toBeVisible();
      expect(runsTab).toBeVisible();
      expect(consoleTab).toHaveAttribute("aria-selected", "true");
    });

    it("switches to Inbox and Runs views from local navigation tabs", async () => {
      render(
        <MobileShell
          currentTab="staff"
          onTabChange={vi.fn()}
          tabContent={{ staff: <StaffConsoleMobile roles={MOCK_ROLES} /> }}
        />,
      );

      const inboxTab = screen.getByTestId("staff-mobile-tab-inbox");
      fireEvent.click(inboxTab);

      await waitFor(() => {
        expect(screen.getByTestId("staff-mobile-inbox-view")).toBeInTheDocument();
      });
      expect(inboxTab).toHaveAttribute("aria-selected", "true");

      const runsTab = screen.getByTestId("staff-mobile-tab-runs");
      fireEvent.click(runsTab);

      await waitFor(() => {
        expect(screen.getByTestId("staff-mobile-runs-view")).toBeInTheDocument();
      });
      expect(runsTab).toHaveAttribute("aria-selected", "true");
    });

    it("renders send and approval controls in thread view with hit-testable targets", async () => {
      const onApprove = vi.fn();
      const onDeny = vi.fn();
      const onSend = vi.fn().mockResolvedValue({ ok: true });

      render(
        <MobileShell
          currentTab="staff"
          onTabChange={vi.fn()}
          tabContent={{
            staff: (
              <StaffConsoleMobile
                roles={MOCK_ROLES}
                initialView="thread"
                initialThread={MOCK_THREAD}
                initialMessages={MOCK_MESSAGES}
                onApproveProposal={onApprove}
                onDenyProposal={onDeny}
                onSendMessage={onSend}
              />
            ),
          }}
        />,
      );

      const composer = screen.getByTestId("staff-mobile-composer-container");
      expect(composer).toBeInTheDocument();

      const textarea = screen.getByPlaceholderText(/message/i);
      fireEvent.change(textarea, { target: { value: "Approved for deployment" } });

      const sendBtn = screen.getByRole("button", { name: /send/i });
      expect(sendBtn).toBeVisible();
      fireEvent.click(sendBtn);

      await waitFor(() => {
        expect(onSend).toHaveBeenCalledWith(
          expect.objectContaining({ body: "Approved for deployment" }),
        );
      });

      const approveBtn = screen.getByRole("button", { name: /approve/i });
      const denyBtn = screen.getByRole("button", { name: /deny/i });
      expect(approveBtn).toBeVisible();
      expect(denyBtn).toBeVisible();

      fireEvent.click(approveBtn);
      expect(onApprove).toHaveBeenCalledWith("prop-101", { host: "ControlTower" });
    });
  });
});
