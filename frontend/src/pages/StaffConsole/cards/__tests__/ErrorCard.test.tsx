// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ErrorCard } from "../ErrorCard";
import type { ErrorCardData } from "../cardTypes";

describe("ErrorCard", () => {
  const MOCK_ERROR: ErrorCardData = {
    failure_class: "auth_expired",
    cause: "GitHub CLI token has expired on node DeskComp.",
    remediation: "Run 'gh auth login' or refresh the secret via Settings.",
    node: "DeskComp",
    retryable: true,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders failure classification, plain-language cause, and remediation", () => {
    render(<ErrorCard error={MOCK_ERROR} />);

    expect(screen.getByText(/Authentication Expired/i)).toBeInTheDocument();
    expect(
      screen.getByText(/GitHub CLI token has expired on node DeskComp/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/gh auth login/i)).toBeInTheDocument();
  });

  it("renders node badge if node is provided", () => {
    render(<ErrorCard error={MOCK_ERROR} />);

    expect(screen.getByText("DeskComp")).toBeInTheDocument();
  });

  it("calls onRetry when Retry button is clicked", () => {
    const handleRetry = vi.fn();
    render(
      <ErrorCard
        error={MOCK_ERROR}
        onRetry={handleRetry}
      />
    );

    const retryBtn = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(retryBtn);

    expect(handleRetry).toHaveBeenCalledTimes(1);
  });

  it("hides Retry button when retryable is false", () => {
    const nonRetryable: ErrorCardData = {
      ...MOCK_ERROR,
      retryable: false,
    };

    render(<ErrorCard error={nonRetryable} />);

    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });
});
