// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockUseStaffConsole = vi.hoisted(() => vi.fn());
vi.mock("../../pages/StaffConsole/useStaffConsole", () => ({
  useStaffConsole: mockUseStaffConsole,
}));

import { BarbSidebar } from "../BarbSidebar";

const sendMessage = vi.fn();
const role = {
  name: "barb",
  title: "Barb",
  summary: "Fleet concierge",
  group: "leadership" as const,
  valid: true,
};

beforeEach(() => {
  sendMessage.mockResolvedValue({ ok: true });
  mockUseStaffConsole.mockReturnValue({
    roles: [role],
    selectedRole: "barb",
    activeThread: null,
    openingRole: null,
    messages: [],
    isReconnecting: false,
    error: null,
    currentRole: role,
    sendMessage,
    costGuard: { pending: null, confirm: vi.fn(), cancel: vi.fn() },
    approveProposal: vi.fn(),
    denyProposal: vi.fn(),
    cancelRun: vi.fn(),
    answerRun: vi.fn(),
    dismissError: vi.fn(),
    newConversation: vi.fn(),
    openRole: vi.fn(),
    openThread: vi.fn(),
    closeThread: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BarbSidebar", () => {
  it("auto-focuses the composer textarea immediately when opened on desktop", () => {
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);
    const composer = screen.getByRole("textbox", { name: /staff conversation input/i });
    expect(composer).toHaveFocus();
  });

  it("restores Barb history without sending a message when the sidebar opens", () => {
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);

    expect(mockUseStaffConsole).toHaveBeenCalledWith(
      expect.objectContaining({ initialRole: "barb", restoreInitialRoleThread: true }),
    );
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("navigates directly to fleet pages from the persistent panel", () => {
    const onNavigate = vi.fn();
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole("button", { name: /open projects/i }));
    expect(onNavigate).toHaveBeenCalledWith("projects");
  });

  it("fills and sends an UpstreamDrift starter through Barb chat", async () => {
    render(<BarbSidebar open onClose={vi.fn()} onNavigate={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /triage upstreamdrift/i }));
    const input = screen.getByRole("textbox", { name: /staff conversation input/i });
    expect((input as HTMLTextAreaElement).value).toContain("UpstreamDrift");
    expect((input as HTMLTextAreaElement).value).toContain("review open issues and pull requests in the UpstreamDrift repository");
    expect((input as HTMLTextAreaElement).value).toContain("registered workflows");
    expect(sendMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() =>
      expect(sendMessage).toHaveBeenCalledWith(
        expect.objectContaining({ body: expect.stringContaining("UpstreamDrift") }),
      ),
    );
  });
});
