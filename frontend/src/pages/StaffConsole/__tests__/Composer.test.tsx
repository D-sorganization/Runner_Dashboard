import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { Composer } from "../Composer";
import type { MentionSuggestion } from "../threadTypes";

const mockRoles: MentionSuggestion[] = [
  { name: "barb", title: "Front Door & Triage", group: "leadership" },
  { name: "orchestrator", title: "Multi-Role Coordinator", group: "leadership" },
  { name: "maintenance", title: "Fleet Maintenance", group: "operations" },
  { name: "night-watch", title: "Overnight Watchdog", group: "operations" },
];

describe("Composer (SC-D4)", () => {
  const localStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
      getItem: (key: string) => store[key] || null,
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
      removeItem: (key: string) => {
        delete store[key];
      },
      clear: () => {
        store = {};
      },
    };
  })();

  beforeEach(() => {
    Object.defineProperty(window, "localStorage", {
      value: localStorageMock,
      writable: true,
    });
    localStorage.clear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders composer textarea, voice input button, and send button", () => {
    render(
      <Composer
        threadId="th_test_1"
        onSend={vi.fn()}
        availableRoles={mockRoles}
      />
    );
    expect(screen.getByPlaceholderText(/message staff/i)).not.toBeNull();
    expect(screen.getByRole("button", { name: /send message/i })).not.toBeNull();
  });

  it("submits message on Enter and creates newline on Shift+Enter", async () => {
    const handleSend = vi.fn().mockResolvedValue(undefined);
    render(
      <Composer
        threadId="th_test_1"
        onSend={handleSend}
        availableRoles={mockRoles}
      />
    );

    const textarea = screen.getByPlaceholderText(/message staff/i);

    // Typing with Shift+Enter creates newline without sending
    fireEvent.change(textarea, { target: { value: "Line 1" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(handleSend).not.toHaveBeenCalled();

    // Plain Enter triggers onSend
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(handleSend).toHaveBeenCalledTimes(1);
    expect(handleSend).toHaveBeenCalledWith("Line 1", expect.any(String));
  });

  it("persists draft in localStorage and restores it across remounts", () => {
    const { unmount } = render(
      <Composer
        threadId="th_draft_1"
        onSend={vi.fn()}
        availableRoles={mockRoles}
      />
    );

    const textarea = screen.getByPlaceholderText(/message staff/i);
    fireEvent.change(textarea, { target: { value: "Saved draft content" } });

    expect(localStorage.getItem("staff_console_draft_th_draft_1")).toBe("Saved draft content");

    unmount();

    // Remount
    render(
      <Composer
        threadId="th_draft_1"
        onSend={vi.fn()}
        availableRoles={mockRoles}
      />
    );
    const reloadedTextarea = screen.getByPlaceholderText(/message staff/i) as HTMLTextAreaElement;
    expect(reloadedTextarea.value).toBe("Saved draft content");
  });

  it("clears draft upon successful send", async () => {
    const handleSend = vi.fn().mockResolvedValue(undefined);
    render(
      <Composer
        threadId="th_clear_1"
        onSend={handleSend}
        availableRoles={mockRoles}
      />
    );

    const textarea = screen.getByPlaceholderText(/message staff/i);
    fireEvent.change(textarea, { target: { value: "Dispatch run" } });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(handleSend).toHaveBeenCalled();
      expect(localStorage.getItem("staff_console_draft_th_clear_1")).toBeNull();
    });
  });

  it("shows mention suggestions when typing @ and autocompletes role on selection", async () => {
    render(
      <Composer
        threadId="th_mention_1"
        onSend={vi.fn()}
        availableRoles={mockRoles}
      />
    );

    const textarea = screen.getByPlaceholderText(/message staff/i);
    fireEvent.change(textarea, { target: { value: "Hey @" } });

    // Suggestions menu should be visible
    expect(screen.getByText("Front Door & Triage")).not.toBeNull();
    expect(screen.getByText("Fleet Maintenance")).not.toBeNull();

    // Click Barb
    fireEvent.click(screen.getByText("Front Door & Triage"));

    expect((textarea as HTMLTextAreaElement).value).toBe("Hey @barb ");
  });

  it("shows slash commands when typing / and autocompletes command template", async () => {
    render(
      <Composer
        threadId="th_slash_1"
        onSend={vi.fn()}
        availableRoles={mockRoles}
      />
    );

    const textarea = screen.getByPlaceholderText(/message staff/i);
    fireEvent.change(textarea, { target: { value: "/" } });

    expect(screen.getByText("/dispatch")).not.toBeNull();
    expect(screen.getByText("/review")).not.toBeNull();
    expect(screen.getByText("/status")).not.toBeNull();

    fireEvent.click(screen.getByText("/review"));

    expect((textarea as HTMLTextAreaElement).value).toBe("/review pr: ");
  });

  it("displays retry button and keeps input when send fails", async () => {
    const handleSend = vi.fn().mockRejectedValue(new Error("Network disconnect"));
    render(
      <Composer
        threadId="th_fail_1"
        onSend={handleSend}
        availableRoles={mockRoles}
      />
    );

    const textarea = screen.getByPlaceholderText(/message staff/i);
    fireEvent.change(textarea, { target: { value: "Important query" } });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry/i })).not.toBeNull();
      expect(screen.getByText(/failed to send/i)).not.toBeNull();
    });

    // Content should still be preserved in textarea
    expect((textarea as HTMLTextAreaElement).value).toBe("Important query");
  });
});
