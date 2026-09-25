// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MaintenanceActionModal } from "../MaintenanceActionModal";

describe("MaintenanceActionModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders modal with pre-filled ActionCard, dry-run preview, and Barb routing for mutating action", () => {
    render(
      <MaintenanceActionModal
        isOpen={true}
        target="runner-worker-1"
        actionKey="take_offline"
        isMachine={false}
        onClose={vi.fn()}
      />
    );

    // Modal dialog
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/maintenance action: take offline/i)).toBeInTheDocument();

    // Barb routing indicator (Owner decision 2026-09-23)
    expect(screen.getByText(/routed via barb to maintenance/i)).toBeInTheDocument();

    // ActionCard details
    expect(screen.getByText("maintenance.runner_stop")).toBeInTheDocument();
    expect(screen.getByText("runner-worker-1")).toBeInTheDocument();

    // Dry-run preview shown
    expect(screen.getByTestId("dry-run-preview")).toBeInTheDocument();
    expect(screen.getByText(/dry-run preview/i)).toBeInTheDocument();
    expect(screen.getByText(/Check active jobs on runner-worker-1 and drain if busy/i)).toBeInTheDocument();
    expect(screen.getByText(/Apply maintenance\.runner_stop on host/i)).toBeInTheDocument();
    expect(screen.getByText(/Verify runner status transitions cleanly to stopped\/offline/i)).toBeInTheDocument();

    // Action buttons
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });

  it("renders read-only diagnose action with direct maintenance attribution", () => {
    render(
      <MaintenanceActionModal
        isOpen={true}
        target="runner-worker-1"
        actionKey="diagnose"
        isMachine={false}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(/maintenance action: diagnose/i)).toBeInTheDocument();
    expect(screen.getByText("maintenance.diagnose")).toBeInTheDocument();
    expect(screen.queryByText(/routed via barb/i)).not.toBeInTheDocument();
  });

  it("executes action after approval and displays verified status change", async () => {
    const handleExecute = vi.fn().mockResolvedValue({
      success: true,
      verified: true,
      verification_message: "Runner 'runner-worker-1' verified stopped",
    });
    const handleSuccess = vi.fn();

    render(
      <MaintenanceActionModal
        isOpen={true}
        target="runner-worker-1"
        actionKey="take_offline"
        isMachine={false}
        onClose={vi.fn()}
        onExecuteAction={handleExecute}
        onSuccess={handleSuccess}
      />
    );

    const approveBtn = screen.getByRole("button", { name: /approve/i });
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(handleExecute).toHaveBeenCalledTimes(1);
      expect(handleExecute).toHaveBeenCalledWith("runner-worker-1", "take_offline", false);
    });

    await waitFor(() => {
      expect(screen.getByText(/verified: Runner 'runner-worker-1' verified stopped/i)).toBeInTheDocument();
      expect(handleSuccess).toHaveBeenCalledTimes(1);
    });
  });

  it("calls onOpenStaffConsole with target and role when staff console button clicked", () => {
    const handleOpenConsole = vi.fn();
    render(
      <MaintenanceActionModal
        isOpen={true}
        target="runner-worker-1"
        actionKey="take_offline"
        isMachine={false}
        onClose={vi.fn()}
        onOpenStaffConsole={handleOpenConsole}
      />
    );

    const consoleBtn = screen.getByRole("button", { name: /open in staff console/i });
    fireEvent.click(consoleBtn);

    expect(handleOpenConsole).toHaveBeenCalledTimes(1);
    expect(handleOpenConsole).toHaveBeenCalledWith("runner-worker-1", "take_offline", "barb");
  });

  it("does not render when isOpen is false", () => {
    const { container } = render(
      <MaintenanceActionModal
        isOpen={false}
        target="runner-worker-1"
        actionKey="take_offline"
        isMachine={false}
        onClose={vi.fn()}
      />
    );

    expect(container.firstChild).toBeNull();
  });
});
