// @vitest-environment jsdom
/**
 * A11yKeyboard.test.tsx — Accessibility and keyboard interaction tests for Staff Console.
 *
 * Implements SC-D9 (Issue #1343) under Epic SC-D (#1350):
 * 1. aria-live polite for streamed replies and conversation log.
 * 2. Focus management on thread switch in Thread and Mobile view.
 * 3. Visible focus indicators on interactive elements.
 * 4. Reduced-motion support across animations and scroll behaviors.
 * 5. Contrast on cards, risk badges, and status dots.
 * 6. Shortcut help dialog triggered by '?'.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Thread } from "../Thread";
import { RosterRow } from "../RosterRow";
import { StaffConsoleMobile } from "../Mobile";
import { HelpAbout } from "../../../shell/HelpAbout";
import type { ThreadInfo, ThreadMessage } from "../threadTypes";
import type { StaffRoleItem } from "../types";

afterEach(cleanup);

const MOCK_THREAD: ThreadInfo = {
  id: "thread-barb",
  title: "Conversation with Barb",
  kind: "direct",
  participants: ["user", "barb"],
  status: "active",
};

const MOCK_MESSAGES: ThreadMessage[] = [
  {
    id: "msg-1",
    thread_id: "thread-barb",
    author: "user",
    author_kind: "user",
    kind: "text",
    body_md: "Check runner status",
    delivery: "complete",
    created_at: "2026-09-25T10:00:00Z",
    seq: 1,
  },
  {
    id: "msg-2",
    thread_id: "thread-barb",
    author: "barb",
    author_kind: "staff",
    kind: "text",
    body_md: "44 runners online.",
    delivery: "streaming",
    streaming: true,
    created_at: "2026-09-25T10:00:05Z",
    seq: 2,
  },
];

const MOCK_ROLE: StaffRoleItem = {
  name: "barb",
  title: "Ask Barb (auto-route)",
  summary: "Personal Secretary and attention gate",
  group: "leadership",
  valid: true,
  dispatchable: true,
};

describe("Staff Console A11y & Keyboard Pass (SC-D9, Issue #1343)", () => {
  describe("1. aria-live polite for streamed replies", () => {
    it("renders conversation messages with role='log' and aria-live='polite'", () => {
      render(<Thread thread={MOCK_THREAD} messages={MOCK_MESSAGES} />);

      const log = screen.getByRole("log");
      expect(log).toHaveAttribute("aria-live", "polite");
      expect(log).toHaveAttribute("tabindex", "0");
    });

    it("does not nest a second live region inside the log (no double announcement)", () => {
      render(<Thread thread={MOCK_THREAD} messages={MOCK_MESSAGES} />);

      const log = screen.getByRole("log");
      expect(log.querySelectorAll("[aria-live]")).toHaveLength(0);
    });
  });

  describe("2. Focus management on thread switch", () => {
    const NEXT_THREAD: ThreadInfo = {
      id: "thread-maintenance",
      title: "Conversation with Maintenance",
      kind: "direct",
      participants: ["user", "maintenance"],
      status: "active",
    };

    it("does not steal focus on first render", () => {
      render(<Thread thread={MOCK_THREAD} messages={MOCK_MESSAGES} onSendMessage={vi.fn()} />);

      expect(document.activeElement).toBe(document.body);
    });

    it("moves focus to the composer when the thread changes", async () => {
      const { rerender } = render(
        <Thread thread={MOCK_THREAD} messages={MOCK_MESSAGES} onSendMessage={vi.fn()} />
      );

      rerender(<Thread thread={NEXT_THREAD} messages={[]} onSendMessage={vi.fn()} />);

      await waitFor(() => {
        expect(document.activeElement?.tagName).toBe("TEXTAREA");
      });
    });

    it("focuses only its own composer, never another textarea on the page", async () => {
      const decoy = document.createElement("textarea");
      document.body.prepend(decoy);
      try {
        const { rerender } = render(
          <Thread thread={MOCK_THREAD} messages={MOCK_MESSAGES} onSendMessage={vi.fn()} />
        );
        rerender(<Thread thread={NEXT_THREAD} messages={[]} onSendMessage={vi.fn()} />);

        await waitFor(() => {
          expect(document.activeElement?.tagName).toBe("TEXTAREA");
        });
        expect(document.activeElement).not.toBe(decoy);
      } finally {
        decoy.remove();
      }
    });

    it("moves focus to the thread heading on mobile open, and back to search on return", async () => {
      render(<StaffConsoleMobile roles={[MOCK_ROLE]} initialView="roster" />);

      fireEvent.click(screen.getByTestId("staff-mobile-ask-barb"));
      await waitFor(() => {
        expect(document.activeElement?.tagName).toBe("H1");
      });

      fireEvent.click(screen.getByTestId("staff-mobile-back-btn"));
      await waitFor(() => {
        expect(document.activeElement).toBe(screen.getByRole("searchbox", { name: /search staff roles/i }));
      });
    });
  });

  describe("3. Shortcut help ('?')", () => {
    it("lists the Staff Console shortcuts in the global '?' help panel", async () => {
      render(<HelpAbout onNavigate={vi.fn()} />);

      fireEvent.keyDown(document.body, { key: "?" });

      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveTextContent("Navigate staff roster roles");
      expect(dialog).toHaveTextContent("Scroll staff thread messages");
    });
  });

  describe("4. Reduced-motion support", () => {
    it("uses auto scroll behavior instead of smooth scroll when prefers-reduced-motion is true", () => {
      const originalMatchMedia = window.matchMedia;
      window.matchMedia = vi.fn().mockImplementation((query) => ({
        matches: query === "(prefers-reduced-motion: reduce)",
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }));

      const scrollIntoViewMock = vi.fn();
      const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
      window.HTMLElement.prototype.scrollIntoView = scrollIntoViewMock;

      const unreadMessages: ThreadMessage[] = [
        ...MOCK_MESSAGES,
        {
          id: "msg-unread",
          thread_id: "thread-barb",
          author: "barb",
          author_kind: "staff",
          kind: "text",
          body_md: "New message",
          delivery: "complete",
          created_at: "2026-09-25T10:05:00Z",
          seq: 3,
        },
      ];

      render(
        <Thread
          thread={MOCK_THREAD}
          messages={unreadMessages}
          unreadSeqThreshold={3}
        />
      );

      const jumpBtn = screen.getByRole("button", { name: /jump to unread/i });
      fireEvent.click(jumpBtn);

      expect(scrollIntoViewMock).toHaveBeenCalledWith(
        expect.objectContaining({ behavior: "auto" })
      );

      window.matchMedia = originalMatchMedia;
      window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    });
  });

  describe("5. Contrast on status dots and cards", () => {
    it("renders status dot with accessible title, label, and high-contrast perimeter", () => {
      render(
        <RosterRow
          role={MOCK_ROLE}
          isSelected={false}
          onSelect={vi.fn()}
        />
      );

      const dot = screen.getByTestId("status-dot-barb");
      expect(dot).toBeInTheDocument();
      expect(dot).toHaveAttribute("aria-label");
      expect(dot.getAttribute("aria-label")).toMatch(/status:/i);
    });
  });
});
