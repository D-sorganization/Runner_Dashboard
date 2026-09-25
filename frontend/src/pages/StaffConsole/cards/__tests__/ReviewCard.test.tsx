// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ReviewCard } from "../ReviewCard";
import type { ReviewCardData } from "../cardTypes";

describe("ReviewCard", () => {
  const MOCK_REVIEW: ReviewCardData = {
    pr_number: 1411,
    pr_title: "feat(staff): staff roster sidebar",
    pr_url: "https://github.com/D-sorganization/Runner_Dashboard/pull/1411",
    verdict: "approved",
    summary: "Roster sidebar conforms to SC-D1 layout and meets all performance criteria.",
    findings: [
      "Keyboard accessibility implemented cleanly with ArrowUp/Down and Enter.",
      "Pinning logic properly bounds pinned items to localStorage.",
      "Zero unhandled edge cases observed.",
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders PR reference, verdict badge, summary, and findings", () => {
    render(<ReviewCard review={MOCK_REVIEW} />);

    expect(screen.getByText(/PR #1411 · feat/i)).toBeInTheDocument();
    expect(screen.getByText(/APPROVED/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Roster sidebar conforms to SC-D1/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Keyboard accessibility implemented cleanly/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Pinning logic properly bounds pinned items/i)
    ).toBeInTheDocument();
  });

  it("links to GitHub pull request", () => {
    render(<ReviewCard review={MOCK_REVIEW} />);

    const link = screen.getByRole("link", { name: /view pr/i });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/pull/1411"
    );
  });
});
