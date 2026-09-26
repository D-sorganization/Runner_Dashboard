// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HandoffCard } from "../HandoffCard";
import type { HandoffCardData } from "../cardTypes";

describe("HandoffCard", () => {
  const MOCK_HANDOFF: HandoffCardData = {
    from_role: "barb",
    to_role: "librarian",
    reason: "Prompt requests documentation verification for architectural decision records.",
    available_alternatives: [
      { name: "cartographer", title: "Cartographer" },
      { name: "fleet-critic", title: "Fleet Critic" },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders from and to roles and reason", () => {
    render(<HandoffCard handoff={MOCK_HANDOFF} />);

    expect(screen.getByText(/barb/i)).toBeInTheDocument();
    expect(screen.getByText(/librarian/i)).toBeInTheDocument();
    expect(
      screen.getByText(/documentation verification for architectural/i)
    ).toBeInTheDocument();
  });

  it("offers to continue with the target role (#1548)", () => {
    const onFollow = vi.fn();
    render(<HandoffCard handoff={MOCK_HANDOFF} onFollow={onFollow} />);

    fireEvent.click(screen.getByRole("button", { name: "Continue with Librarian" }));

    expect(onFollow).toHaveBeenCalledWith("librarian");
  });

  it("has no continue button without a follow handler", () => {
    render(<HandoffCard handoff={MOCK_HANDOFF} />);

    expect(screen.queryByRole("button", { name: /Continue with/ })).not.toBeInTheDocument();
  });

  it("renders alternative specialists and handles re-routing", () => {
    const handleReroute = vi.fn();
    render(
      <HandoffCard
        handoff={MOCK_HANDOFF}
        onReroute={handleReroute}
      />
    );

    const overrideBtn = screen.getByRole("button", {
      name: /send to someone else/i,
    });
    fireEvent.click(overrideBtn);

    const cartographerBtn = screen.getByRole("button", {
      name: /cartographer/i,
    });
    fireEvent.click(cartographerBtn);

    expect(handleReroute).toHaveBeenCalledWith("cartographer");
  });
});
