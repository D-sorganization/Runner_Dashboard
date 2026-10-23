// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MobileShell } from "../MobileShell";

const mockUseStaffConsole = vi.hoisted(() => vi.fn());
vi.mock("../../pages/StaffConsole/useStaffConsole", () => ({
  useStaffConsole: mockUseStaffConsole,
}));
vi.mock("../../hooks/useBreakpoint", () => ({ useBreakpoint: () => "md" }));

const role = {
  name: "barb",
  title: "Barb",
  summary: "Fleet concierge",
  group: "leadership" as const,
  valid: true,
};

beforeEach(() => {
  mockUseStaffConsole.mockReturnValue({
    roles: [role], activeThread: null, messages: [], isReconnecting: false,
    error: null, sendMessage: vi.fn(), costGuard: { pending: null, confirm: vi.fn(), cancel: vi.fn() },
    approveProposal: vi.fn(), denyProposal: vi.fn(), cancelRun: vi.fn(),
    answerRun: vi.fn(), dismissError: vi.fn(), newConversation: vi.fn(), openRole: vi.fn(),
  });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("MobileShell Barb access", () => {
  it("closes the mobile modal after quick navigation and preserves Barb for reopening", async () => {
    const onTabChange = vi.fn();
    const { rerender } = render(
      <MobileShell currentTab="projects" onTabChange={onTabChange}><div>Projects</div></MobileShell>,
    );

    const trigger = screen.getByTestId("mobile-ask-barb");
    trigger.focus();
    fireEvent.click(trigger);
    const panel = await screen.findByRole("dialog", { name: "Ask Barb" });

    fireEvent.click(screen.getByRole("button", { name: /open fleet command/i }));
    expect(onTabChange).toHaveBeenCalledWith("fleet-command");
    expect(screen.queryByRole("dialog", { name: "Ask Barb" })).not.toBeInTheDocument();
    expect(screen.getByRole("main")).not.toHaveAttribute("inert");
    expect(trigger).toHaveFocus();
    rerender(<MobileShell currentTab="fleet-command" onTabChange={onTabChange}><div>Fleet Command</div></MobileShell>);
    expect(screen.getByRole("main")).toHaveTextContent("Fleet Command");
    fireEvent.click(screen.getByTestId("mobile-ask-barb"));
    expect(await screen.findByRole("dialog", { name: "Ask Barb" })).toBe(panel);
  });

  it("closes the mobile modal after a registered assistant navigation result", async () => {
    render(
      <MobileShell currentTab="projects" onTabChange={vi.fn()}><div>Projects</div></MobileShell>,
    );
    fireEvent.click(screen.getByTestId("mobile-ask-barb"));
    await screen.findByRole("dialog", { name: "Ask Barb" });

    fireEvent(
      window,
      new CustomEvent("runner-dashboard:navigate", { detail: { tabId: "queue" } }),
    );

    expect(screen.queryByRole("dialog", { name: "Ask Barb" })).not.toBeInTheDocument();
    expect(screen.getByRole("main")).not.toHaveAttribute("inert");
  });

  it("leaves the mobile modal open for unregistered assistant navigation", async () => {
    render(
      <MobileShell currentTab="projects" onTabChange={vi.fn()}><div>Projects</div></MobileShell>,
    );
    fireEvent.click(screen.getByTestId("mobile-ask-barb"));
    const panel = await screen.findByRole("dialog", { name: "Ask Barb" });

    fireEvent(
      window,
      new CustomEvent("runner-dashboard:navigate", { detail: { tabId: "https://example.com" } }),
    );

    expect(screen.getByRole("dialog", { name: "Ask Barb" })).toBe(panel);
  });

  it("traps keyboard focus in the modal and restores it to the trigger on close", async () => {
    render(
      <MobileShell currentTab="projects" onTabChange={vi.fn()}><div>Projects</div></MobileShell>,
    );
    const trigger = screen.getByTestId("mobile-ask-barb");
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = await screen.findByRole("dialog", { name: "Ask Barb" });
    const close = screen.getByRole("button", { name: "Close Ask Barb" });
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ));
    const last = focusable[focusable.length - 1];
    expect(close).toHaveFocus();
    const shellSiblings = Array.from(dialog.parentElement!.children).filter((element) => element !== dialog);
    expect(shellSiblings.length).toBeGreaterThan(0);
    shellSiblings.forEach((element) => expect(element).toHaveAttribute("inert"));

    last.focus();
    const tab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
    dialog.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(close).toHaveFocus();

    const shiftTab = new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true });
    close.dispatchEvent(shiftTab);
    expect(shiftTab.defaultPrevented).toBe(true);
    expect(last).toHaveFocus();

    fireEvent.click(close);
    expect(trigger).toHaveFocus();
  });
});
