// @vitest-environment jsdom
/**
 * Composer.test.tsx — Unit tests for Staff Console Composer, keyboard navigation,
 * mention autocomplete, slash commands, draft persistence, and reliable send (SC-D4, Issue #1318).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "../Composer";
import type { StaffRoleItem } from "../types";

const MOCK_ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Executive Secretary", summary: "Attention gate" },
  { name: "board", title: "Board of Directors", summary: "Governance" },
  { name: "librarian", title: "Librarian", summary: "Documentation" },
  { name: "fleet-maintenance", title: "Fleet Maintenance", summary: "Operations" },
];

afterEach(cleanup);

describe("Composer Component", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders input, send button, and voice input button", () => {
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);

    expect(screen.getByPlaceholderText(/message barb or type \/dispatch/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /voice/i })).toBeInTheDocument();
  });

  it("submits message on Enter and creates newline on Shift+Enter", () => {
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);

    // Shift+Enter does NOT submit
    fireEvent.change(textarea, { target: { value: "Hello world" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    // Plain Enter submits
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith(
      expect.objectContaining({
        body: "Hello world",
        idempotencyKey: expect.any(String),
      })
    );
  });

  it("shows @mention autocomplete popup and selects role via keyboard", async () => {
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);

    // Type @ to trigger mention menu
    fireEvent.change(textarea, { target: { value: "Hello @" } });
    expect(screen.getByRole("listbox", { name: /role mentions/i })).toBeInTheDocument();
    expect(screen.getByText("Executive Secretary")).toBeInTheDocument();

    // Keyboard navigate down to 'board' and hit Enter
    fireEvent.keyDown(textarea, { key: "ArrowDown" });
    fireEvent.keyDown(textarea, { key: "Enter" });

    // The mention is inserted into the textarea
    expect(textarea).toHaveValue("Hello @board ");
    // Autocomplete menu is dismissed
    expect(screen.queryByRole("listbox", { name: /role mentions/i })).toBeNull();
  });

  it("shows slash command autocomplete and selects command via keyboard", async () => {
    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={vi.fn()} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);

    // Type / at start
    fireEvent.change(textarea, { target: { value: "/" } });
    expect(screen.getByRole("listbox", { name: /slash commands/i })).toBeInTheDocument();
    expect(screen.getByText("/dispatch")).toBeInTheDocument();
    expect(screen.getByText("/review")).toBeInTheDocument();
    expect(screen.getAllByText("/status").length).toBeGreaterThan(0);

    // Select with keyboard Enter
    fireEvent.keyDown(textarea, { key: "Enter" });

    // The command is inserted
    expect(textarea).toHaveValue("/dispatch ");
    expect(screen.queryByRole("listbox", { name: /slash commands/i })).toBeNull();
  });

  it("persists draft in localStorage per thread and clears upon successful send", async () => {
    const onSend = vi.fn().mockResolvedValue({ ok: true });
    const { unmount } = render(
      <Composer threadId="thread-abc" roles={MOCK_ROLES} onSendMessage={onSend} />
    );

    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Draft message for abc" } });

    expect(localStorage.getItem("staff-console:draft:thread-abc")).toBe("Draft message for abc");

    // Unmount and remount with same threadId restores draft
    unmount();
    render(<Composer threadId="thread-abc" roles={MOCK_ROLES} onSendMessage={onSend} />);
    const restoredTextarea = screen.getByPlaceholderText(/message barb/i);
    expect(restoredTextarea).toHaveValue("Draft message for abc");

    // Send successfully
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => {
      expect(onSend).toHaveBeenCalled();
      expect(localStorage.getItem("staff-console:draft:thread-abc")).toBeNull();
      expect(restoredTextarea).toHaveValue("");
    });
  });

  it("preserves draft and allows retry with same Idempotency-Key on send failure", async () => {
    const onSend = vi
      .fn()
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({ ok: true });

    render(<Composer threadId="thread-1" roles={MOCK_ROLES} onSendMessage={onSend} />);

    const textarea = screen.getByPlaceholderText(/message barb/i);
    fireEvent.change(textarea, { target: { value: "Important request" } });

    // Attempt send -> fails
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/failed to send message/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });

    // Draft is kept
    expect(textarea).toHaveValue("Important request");
    const firstCallKey = onSend.mock.calls[0][0].idempotencyKey;
    expect(firstCallKey).toBeDefined();

    // Click retry
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledTimes(2);
      const secondCallKey = onSend.mock.calls[1][0].idempotencyKey;
      // Must match same idempotency key for deduplication!
      expect(secondCallKey).toBe(firstCallKey);
    });
  });
});
