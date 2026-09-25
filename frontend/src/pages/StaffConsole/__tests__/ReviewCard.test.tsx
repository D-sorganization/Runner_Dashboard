/**
 * ReviewCard.test.tsx — Unit tests for ReviewCard (SC-D5, Issue #1319).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { ReviewCard } from "../cards/ReviewCard";
import type { ReviewCardData } from "../cards/cardTypes";

describe("ReviewCard", () => {
  const reviewApproved: ReviewCardData = {
    pr_number: 1414,
    pr_title: "fix(staff): resolve eslint warnings",
    pr_url: "https://github.com/D-sorganization/Runner_Dashboard/pull/1414",
    verdict: "APPROVED",
    summary: "All lint rules pass cleanly and unit tests provide 100% coverage.",
    findings: ["Helpers cleanly extracted to threadUtils", "No circular dependencies observed"],
    reviewer: "fleet-critic",
    reviewed_at: "2026-09-25T11:00:00Z",
  };

  it("renders PR title, number link, verdict, summary and findings", () => {
    render(<ReviewCard review={reviewApproved} />);
    expect(screen.getByText(/fix\(staff\): resolve eslint warnings/i)).toBeInTheDocument();
    expect(screen.getByText("APPROVED")).toBeInTheDocument();
    expect(screen.getByText(/All lint rules pass cleanly/i)).toBeInTheDocument();
    expect(screen.getByText("Helpers cleanly extracted to threadUtils")).toBeInTheDocument();
    expect(screen.getByText("No circular dependencies observed")).toBeInTheDocument();

    const link = screen.getByRole("link", { name: /#1414/i });
    expect(link).toHaveAttribute("href", "https://github.com/D-sorganization/Runner_Dashboard/pull/1414");
  });

  it("renders CHANGES_REQUESTED verdict styling correctly", () => {
    const reviewChanges: ReviewCardData = {
      ...reviewApproved,
      verdict: "CHANGES_REQUESTED",
      findings: ["Missing error boundary in Thread component"],
    };
    render(<ReviewCard review={reviewChanges} />);
    expect(screen.getByText("CHANGES_REQUESTED")).toBeInTheDocument();
    expect(screen.getByText("Missing error boundary in Thread component")).toBeInTheDocument();
  });
});
