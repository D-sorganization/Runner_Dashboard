/**
 * ActionCard.test.tsx — Unit tests for ActionCard (SC-D5, Issue #1319).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { ActionCard } from "../cards/ActionCard";
import type { ActionProposalData } from "../cards/cardTypes";

describe("ActionCard", () => {
  const baseProposal: ActionProposalData = {
    id: "prop-123",
    action: "maintenance.runner_restart",
    target: "runner-worker-4",
    risk: "high",
    state: "proposed",
    params: { node: "worker-4", force: false },
    expires_at: "2099-01-01T00:00:00Z",
  };

  it("renders action name, target, and risk badge", () => {
    render(<ActionCard proposal={baseProposal} />);
    expect(screen.getByText("maintenance.runner_restart")).toBeInTheDocument();
    expect(screen.getByText(/runner-worker-4/)).toBeInTheDocument();
    expect(screen.getByText(/high/i)).toBeInTheDocument();
    expect(screen.getByTestId("approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("deny-btn")).toBeInTheDocument();
  });

  it("double-click idempotency: clicking Approve repeatedly executes only once", async () => {
    const onApprove = vi.fn().mockImplementation(() => new Promise((r) => setTimeout(r, 100)));
    render(<ActionCard proposal={baseProposal} onApprove={onApprove} />);

    const approveBtn = screen.getByTestId("approve-btn");
    // Double click rapidly
    fireEvent.click(approveBtn);
    fireEvent.click(approveBtn);
    fireEvent.click(approveBtn);

    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onApprove).toHaveBeenCalledWith("prop-123", { node: "worker-4", force: false });
  });

  it("double-click idempotency: clicking Deny repeatedly executes only once", async () => {
    const onDeny = vi.fn().mockImplementation(() => new Promise((r) => setTimeout(r, 100)));
    render(<ActionCard proposal={baseProposal} onDeny={onDeny} />);

    const denyBtn = screen.getByTestId("deny-btn");
    fireEvent.click(denyBtn);
    fireEvent.click(denyBtn);

    expect(onDeny).toHaveBeenCalledTimes(1);
    expect(onDeny).toHaveBeenCalledWith("prop-123", undefined);
  });

  it("stale card state: disables actions with explanation when proposal is expired", () => {
    const expiredProposal: ActionProposalData = {
      ...baseProposal,
      state: "expired",
      is_expired: true,
    };

    render(<ActionCard proposal={expiredProposal} />);
    expect(screen.queryByTestId("approve-btn")).toBeNull();
    expect(screen.queryByTestId("deny-btn")).toBeNull();
    expect(screen.getByText(/proposal expired/i)).toBeInTheDocument();
  });

  it("decided card state: renders who decided and when, with actions disabled", () => {
    const decidedProposal: ActionProposalData = {
      ...baseProposal,
      state: "approved",
      decided_by: "dieterolson",
      decided_at: "2026-09-25T10:00:00Z",
    };

    render(<ActionCard proposal={decidedProposal} />);
    expect(screen.queryByTestId("approve-btn")).toBeNull();
    expect(screen.getByText(/approved by dieterolson/i)).toBeInTheDocument();
  });

  it("allows editing parameters before approving", async () => {
    const onApprove = vi.fn();
    render(<ActionCard proposal={baseProposal} onApprove={onApprove} />);

    const editBtn = screen.getByTestId("edit-params-btn");
    fireEvent.click(editBtn);

    const textarea = screen.getByTestId("params-textarea") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: JSON.stringify({ node: "worker-99", force: true }) } });

    const approveBtn = screen.getByTestId("approve-btn");
    fireEvent.click(approveBtn);

    expect(onApprove).toHaveBeenCalledWith("prop-123", { node: "worker-99", force: true });
  });
});
