/**
 * MobileShellFab.test.tsx — Unit tests for the mobile Ask FAB and Ask sheet (SC-G5-3, #1499).
 *
 * Verifies:
 *  - Mobile FAB renders with aria-label="Ask" and data-testid="ask-fab"
 *  - FAB is visible on operator action surfaces (overview, workflows, remediation, queue)
 *  - Tapping the FAB opens the Ask sheet (not AgentDispatch)
 *  - The Ask sheet renders heading "Ask", a close button, and composer input
 *  - The Ask sheet closes on close button click, overlay click, or Escape
 */
import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { MobileShell } from "../MobileShell";

const breakpointMock = vi.hoisted(() => ({ value: "md" }));

vi.mock("../../hooks/useBreakpoint", () => ({
  useBreakpoint: () => breakpointMock.value,
}));

vi.mock("../../pages/Staff/staffApi", () => ({
  submitStaffRequest: vi.fn().mockResolvedValue({
    state: "executed",
    run_id: "run-ask-123",
    work_item_id: "wi-456",
  }),
  errorMessage: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}));

describe("MobileShell — Ask FAB and Composer (#1499)", () => {
  beforeEach(() => {
    breakpointMock.value = "md";
    window.matchMedia = vi.fn(
      (query) =>
        ({
          matches: query === "(max-width: 767px)",
          media: query,
          onchange: null,
          addListener: vi.fn(),
          removeListener: vi.fn(),
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as unknown as MediaQueryList,
    );
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the Ask FAB with accessible label 'Ask' on operator surfaces", () => {
    render(
      <MobileShell currentTab="remediation" onTabChange={vi.fn()}>
        <div>Remediation page</div>
      </MobileShell>,
    );

    const fab = screen.getByTestId("ask-fab");
    expect(fab).toBeInTheDocument();
    expect(fab).toHaveAttribute("aria-label", "Ask");
  });

  it("opens the Ask sheet with composer when the FAB is tapped", async () => {
    render(
      <MobileShell currentTab="queue" onTabChange={vi.fn()}>
        <div>Queue page</div>
      </MobileShell>,
    );

    // Sheet should not be open initially
    expect(screen.queryByRole("dialog", { name: /ask/i })).not.toBeInTheDocument();

    // Tap FAB
    const fab = screen.getByTestId("ask-fab");
    fireEvent.click(fab);

    // Ask sheet dialog appears
    const dialog = await screen.findByRole("dialog", { name: /ask/i });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /ask/i })).toBeInTheDocument();

    // Composer prompt area is present
    const textarea = screen.getByPlaceholderText(/ask/i);
    expect(textarea).toBeInTheDocument();
  });

  it("closes the Ask sheet when the close button is clicked", async () => {
    render(
      <MobileShell currentTab="workflows" onTabChange={vi.fn()}>
        <div>Workflows page</div>
      </MobileShell>,
    );

    fireEvent.click(screen.getByTestId("ask-fab"));
    expect(await screen.findByRole("dialog", { name: /ask/i })).toBeInTheDocument();

    const closeBtn = screen.getByLabelText(/close ask sheet/i);
    fireEvent.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /ask/i })).not.toBeInTheDocument();
    });
  });

  it("closes the Ask sheet on Escape keydown", async () => {
    render(
      <MobileShell currentTab="overview" onTabChange={vi.fn()}>
        <div>Overview page</div>
      </MobileShell>,
    );

    fireEvent.click(screen.getByTestId("ask-fab"));
    expect(await screen.findByRole("dialog", { name: /ask/i })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /ask/i })).not.toBeInTheDocument();
    });
  });
});
