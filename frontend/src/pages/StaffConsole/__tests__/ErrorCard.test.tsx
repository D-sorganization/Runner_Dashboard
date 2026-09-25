/**
 * ErrorCard.test.tsx — Unit tests for ErrorCard (SC-D5, Issue #1319).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { ErrorCard } from "../cards/ErrorCard";
import type { ErrorCardData } from "../cards/cardTypes";

describe("ErrorCard", () => {
  const errorData: ErrorCardData = {
    failure_class: "auth_token_expired",
    message: "GitHub CLI authentication session expired on node.",
    node: "oglaptop-linux",
    command: "gh auth login --hostname github.com",
    remediation: "Re-authenticate using the command below.",
    retryable: true,
  };

  it("renders plain-language cause, node and remediation command", () => {
    render(<ErrorCard error={errorData} />);
    expect(screen.getByText(/Authentication Token Expired/i)).toBeInTheDocument();
    expect(screen.getByText(/GitHub CLI authentication session expired/i)).toBeInTheDocument();
    expect(screen.getByText(/oglaptop-linux/)).toBeInTheDocument();
    expect(screen.getByText("gh auth login --hostname github.com")).toBeInTheDocument();
    expect(screen.getByTestId("copy-cmd-btn")).toBeInTheDocument();
    expect(screen.getByTestId("retry-turn-btn")).toBeInTheDocument();
  });

  it("double-click idempotency: retry executes only once on rapid clicks", () => {
    const onRetry = vi.fn().mockImplementation(() => new Promise((r) => setTimeout(r, 100)));
    render(<ErrorCard error={errorData} onRetry={onRetry} />);

    const retryBtn = screen.getByTestId("retry-turn-btn");
    fireEvent.click(retryBtn);
    fireEvent.click(retryBtn);

    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("hides retry button when error is not retryable", () => {
    const nonRetryable: ErrorCardData = {
      ...errorData,
      retryable: false,
    };
    render(<ErrorCard error={nonRetryable} />);
    expect(screen.queryByTestId("retry-turn-btn")).toBeNull();
  });
});
