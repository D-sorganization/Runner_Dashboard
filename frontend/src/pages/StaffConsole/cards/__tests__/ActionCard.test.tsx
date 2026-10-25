// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ActionCard } from "../ActionCard";
import type { ActionProposalData } from "../cardTypes";

describe("ActionCard", () => {
  const MOCK_PROPOSAL: ActionProposalData = {
    id: "prop-123",
    action_name: "maintenance.runner_restart",
    target: "runner-worker-4",
    risk_level: "medium",
    status: "pending",
    proposed_by: "maintenance",
    params: { graceful: true, timeout_seconds: 30 },
    expires_at: new Date(Date.now() + 3600 * 1000).toISOString(),
    description: "Restart stalled listener on runner-worker-4",
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders action details, target, and risk badge", () => {
    render(<ActionCard proposal={MOCK_PROPOSAL} />);

    expect(screen.getByText("maintenance.runner_restart")).toBeInTheDocument();
    expect(screen.getByText("runner-worker-4")).toBeInTheDocument();
    expect(screen.getByText(/medium/i)).toBeInTheDocument();
    expect(screen.getByText(/Restart stalled listener/i)).toBeInTheDocument();
  });

  it("calls onApprove once even on double click (double-click idempotency)", () => {
    const handleApprove = vi.fn();
    render(
      <ActionCard
        proposal={MOCK_PROPOSAL}
        onApprove={handleApprove}
      />
    );

    const approveBtn = screen.getByRole("button", { name: /approve/i });
    expect(approveBtn).toBeInTheDocument();

    // Double click rapidly
    fireEvent.click(approveBtn);
    fireEvent.click(approveBtn);

    expect(handleApprove).toHaveBeenCalledTimes(1);
    expect(handleApprove).toHaveBeenCalledWith("prop-123", MOCK_PROPOSAL.params, undefined);
  });

  it("re-enables its buttons when the approval is refused (#1547)", async () => {
    const handleApprove = vi.fn(async () => false);
    render(<ActionCard proposal={MOCK_PROPOSAL} onApprove={handleApprove} />);

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /^approve$/i })).toBeEnabled());
    expect(screen.getByRole("button", { name: /deny/i })).toBeEnabled();
  });

  it("stays disabled while an accepted approval executes", async () => {
    const handleApprove = vi.fn(async () => true);
    render(<ActionCard proposal={MOCK_PROPOSAL} onApprove={handleApprove} />);

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => expect(handleApprove).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: /executing/i })).toBeDisabled();
  });

  it("releases the executing lock when an approved action transitions to failed", async () => {
    const handleApprove = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    const { rerender } = render(<ActionCard proposal={MOCK_PROPOSAL} onApprove={handleApprove} />);

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /executing/i })).toBeDisabled());

    rerender(
      <ActionCard
        proposal={{ ...MOCK_PROPOSAL, status: "approved" }}
        onApprove={handleApprove}
      />,
    );
    rerender(
      <ActionCard
        proposal={{ ...MOCK_PROPOSAL, status: "failed" }}
        onApprove={handleApprove}
      />,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: /^approve$/i })).toBeEnabled());
    expect(screen.getByRole("status")).toHaveTextContent(/action failed.*retry/i);

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    await waitFor(() => expect(handleApprove).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("button", { name: /^approve$/i })).toBeEnabled());
  });

  it("calls onDeny when Deny button is clicked", () => {
    const handleDeny = vi.fn();
    render(
      <ActionCard
        proposal={MOCK_PROPOSAL}
        onDeny={handleDeny}
      />
    );

    const denyBtn = screen.getByRole("button", { name: /deny/i });
    fireEvent.click(denyBtn);

    expect(handleDeny).toHaveBeenCalledTimes(1);
    expect(handleDeny).toHaveBeenCalledWith("prop-123");
  });

  it("displays decision history when already decided", () => {
    const decidedProposal: ActionProposalData = {
      ...MOCK_PROPOSAL,
      status: "approved",
      decided_by: "operator",
      decided_at: "2026-09-25T11:00:00.000Z",
    };

    render(<ActionCard proposal={decidedProposal} />);

    expect(screen.getByText(/approved by dieter/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("disables actions and displays explanation when proposal is expired", () => {
    const expiredProposal: ActionProposalData = {
      ...MOCK_PROPOSAL,
      status: "expired",
      expires_at: "2026-09-24T00:00:00.000Z",
    };

    render(<ActionCard proposal={expiredProposal} />);

    expect(screen.getByText(/proposal expired/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("toggles parameter inspection view", () => {
    render(<ActionCard proposal={MOCK_PROPOSAL} />);

    const paramsBtn = screen.getByRole("button", { name: /params/i });
    fireEvent.click(paramsBtn);

    expect(screen.getByText(/"timeout_seconds": 30/i)).toBeInTheDocument();
  });

  it("renders dry-run preview and planned steps when dry_run is provided", () => {
    const dryRunProposal: ActionProposalData = {
      ...MOCK_PROPOSAL,
      dry_run: {
        planned_steps: [
          "Validate host reachability",
          "Apply maintenance.runner_stop",
          "Verify runner is stopped",
        ],
      },
    };

    render(<ActionCard proposal={dryRunProposal} />);

    expect(screen.getByTestId("dry-run-preview")).toBeInTheDocument();
    expect(screen.getByText(/dry-run preview/i)).toBeInTheDocument();
    expect(screen.getByText(/Validate host reachability/i)).toBeInTheDocument();
    expect(screen.getByText(/Apply maintenance\.runner_stop/i)).toBeInTheDocument();
    expect(screen.getByText(/Verify runner is stopped/i)).toBeInTheDocument();
  });

  it("renders verification confirmation when verification_message is present", () => {
    const verifiedProposal: ActionProposalData = {
      ...MOCK_PROPOSAL,
      status: "approved",
      decided_by: "operator",
      decided_at: "2026-09-25T11:00:00.000Z",
      verification_message: "Runner 'runner-worker-4' verified stopped",
    };

    render(<ActionCard proposal={verifiedProposal} />);

    expect(screen.getByText(/verified: Runner 'runner-worker-4' verified stopped/i)).toBeInTheDocument();
  });

  it("renders routed-by indicator when routed_role is provided", () => {
    const routedProposal: ActionProposalData = {
      ...MOCK_PROPOSAL,
      routed_role: "barb",
    };

    render(<ActionCard proposal={routedProposal} />);

    expect(screen.getByText(/routed via barb to maintenance/i)).toBeInTheDocument();
  });

  describe("approval authority preview (#1807)", () => {
    const HASH = "a".repeat(64);

    it("disables both buttons and shows the reason when can_approve is false", () => {
      const onApprove = vi.fn();
      const onDeny = vi.fn();
      render(
        <ActionCard
          proposal={{ ...MOCK_PROPOSAL, can_approve: false, denial_reason: "Requires owner approval", params_hash: HASH }}
          onApprove={onApprove}
          onDeny={onDeny}
        />,
      );

      const approve = screen.getByRole("button", { name: /approve/i });
      const deny = screen.getByRole("button", { name: /deny/i });
      expect(approve).toBeDisabled();
      expect(deny).toBeDisabled();
      const reason = screen.getByTestId("approval-denial-reason");
      expect(reason).toHaveTextContent("Requires owner approval");
      expect(approve).toHaveAttribute("aria-describedby", reason.id);
      expect(deny).toHaveAttribute("aria-describedby", reason.id);
      expect(approve).toHaveAttribute("title", "Requires owner approval");

      fireEvent.click(approve);
      fireEvent.click(deny);
      expect(onApprove).not.toHaveBeenCalled();
      expect(onDeny).not.toHaveBeenCalled();
    });

    it("falls back to a generic reason when the server gave none", () => {
      render(<ActionCard proposal={{ ...MOCK_PROPOSAL, can_approve: false }} />);
      expect(screen.getByTestId("approval-denial-reason")).toHaveTextContent(/not allowed to approve/i);
    });

    it("keeps the buttons enabled when can_approve is true and passes the reviewed hash", () => {
      const onApprove = vi.fn();
      render(
        <ActionCard
          proposal={{ ...MOCK_PROPOSAL, can_approve: true, denial_reason: null, params_hash: HASH }}
          onApprove={onApprove}
        />,
      );
      expect(screen.queryByTestId("approval-denial-reason")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /approve/i }));
      expect(onApprove).toHaveBeenCalledWith("prop-123", MOCK_PROPOSAL.params, HASH);
    });

    it("leaves the buttons as they were when nothing was previewed", () => {
      render(<ActionCard proposal={MOCK_PROPOSAL} />);
      expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
      expect(screen.getByRole("button", { name: /deny/i })).toBeEnabled();
    });

    it("waits for a pending preview before enabling the buttons", () => {
      render(<ActionCard proposal={MOCK_PROPOSAL} previewPending />);
      expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled();
    });
  });
});

