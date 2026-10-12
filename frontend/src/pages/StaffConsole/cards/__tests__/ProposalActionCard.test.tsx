// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

const api = vi.hoisted(() => ({ getActionProposal: vi.fn() }));
vi.mock("../../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../Staff/staffApi")>()),
  ...api,
}));

import { ProposalActionCard } from "../ProposalActionCard";
import type { ActionProposalData } from "../cardTypes";

const HASH = "d".repeat(64);
const PENDING: ActionProposalData = {
  id: "prop-9",
  action_name: "staff.hold",
  risk_level: "high",
  status: "pending",
  params: { text: "freeze" },
};

describe("ProposalActionCard (#1807)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it("overlays the viewer preview: denied viewers get disabled buttons and the reason", async () => {
    api.getActionProposal.mockResolvedValue({
      id: "prop-9",
      can_approve: false,
      denial_reason:
        "Action staff.hold has risk high and requires owner approval",
      params_hash: HASH,
    });
    render(<ProposalActionCard proposal={PENDING} onApprove={vi.fn()} />);

    // Buttons wait for the preview, then settle disabled with the reason.
    expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled();
    await waitFor(() =>
      expect(screen.getByTestId("approval-denial-reason")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /deny/i })).toBeDisabled();
    expect(screen.getByTestId("approval-denial-reason")).toHaveTextContent(
      /requires owner approval/,
    );
  });

  it("sends the previewed hash on approve and refreshes the preview when refused", async () => {
    api.getActionProposal.mockResolvedValue({
      id: "prop-9",
      can_approve: true,
      denial_reason: null,
      params_hash: HASH,
    });
    const onApprove = vi.fn(async () => false);
    render(<ProposalActionCard proposal={PENDING} onApprove={onApprove} />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /approve/i }));

    expect(onApprove).toHaveBeenCalledWith("prop-9", PENDING.params, HASH);
    await waitFor(() => expect(api.getActionProposal).toHaveBeenCalledTimes(2));
  });

  it("does not preview a decided card", () => {
    render(
      <ProposalActionCard proposal={{ ...PENDING, status: "approved" }} />,
    );
    expect(api.getActionProposal).not.toHaveBeenCalled();
  });

  it("grants nothing when the preview cannot be loaded", async () => {
    api.getActionProposal.mockRejectedValue(new Error("network"));
    const onApprove = vi.fn();
    render(<ProposalActionCard proposal={PENDING} onApprove={onApprove} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    // No hash: the handler gets none, and the console refuses to approve without one.
    expect(onApprove).toHaveBeenCalledWith("prop-9", PENDING.params, undefined);
  });
});
