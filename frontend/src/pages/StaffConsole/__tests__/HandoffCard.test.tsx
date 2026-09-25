/**
 * HandoffCard.test.tsx — Unit tests for HandoffCard (SC-D5, Issue #1319).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { HandoffCard } from "../cards/HandoffCard";
import type { HandoffCardData } from "../cards/cardTypes";

describe("HandoffCard", () => {
  const handoffData: HandoffCardData = {
    from_role: "barb",
    to_role: "librarian",
    reason: "Query requests research on historical commit trends.",
    available_roles: [
      { id: "librarian", name: "Librarian" },
      { id: "fleet-critic", name: "Fleet Critic" },
      { id: "doc-specialist", name: "Doc Specialist" },
    ],
  };

  it("renders from_role and to_role with transfer arrow and reason", () => {
    render(<HandoffCard handoff={handoffData} />);
    expect(screen.getByText(/Barb/i)).toBeInTheDocument();
    expect(screen.getByText(/Librarian/i)).toBeInTheDocument();
    expect(screen.getByText(/historical commit trends/i)).toBeInTheDocument();
  });

  it("send to someone else opens selector and triggers onRedirectHandoff", () => {
    const onRedirect = vi.fn().mockImplementation(() => new Promise((r) => setTimeout(r, 100)));
    render(<HandoffCard handoff={handoffData} onRedirectHandoff={onRedirect} />);

    const overrideBtn = screen.getByTestId("send-someone-else-btn");
    fireEvent.click(overrideBtn);

    const select = screen.getByTestId("handoff-role-select");
    fireEvent.change(select, { target: { value: "doc-specialist" } });

    const confirmBtn = screen.getByTestId("confirm-redirect-btn");
    fireEvent.click(confirmBtn);
    fireEvent.click(confirmBtn);

    expect(onRedirect).toHaveBeenCalledTimes(1);
    expect(onRedirect).toHaveBeenCalledWith("doc-specialist");
  });
});
