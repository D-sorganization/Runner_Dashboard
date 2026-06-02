// @vitest-environment jsdom
/**
 * Tests for EmptyState primitive (#837).
 *
 * TDD: authored alongside the primitive. Covers the idle vs error variants,
 * the Retry affordance (modeled on MaxwellChat), and accessibility.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { EmptyState } from "../EmptyState";

afterEach(cleanup);

describe("EmptyState", () => {
  it("renders title and description for the empty variant", () => {
    render(<EmptyState title="No reports found" description="Generate one first." />);
    expect(screen.getByText("No reports found")).toBeInTheDocument();
    expect(screen.getByText("Generate one first.")).toBeInTheDocument();
  });

  it("does not render a Retry button when no onRetry is given", () => {
    render(<EmptyState title="Nothing here" />);
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("renders a Retry button that fires onRetry (error variant)", () => {
    const onRetry = vi.fn();
    render(
      <EmptyState
        variant="error"
        title="Service unreachable"
        description="Start it from Local Tools."
        onRetry={onRetry}
      />,
    );
    const btn = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("marks the error variant as a polite live region for screen readers", () => {
    render(<EmptyState variant="error" title="Backend error" onRetry={() => {}} />);
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("data-variant", "error");
  });

  it("supports a custom retry label", () => {
    render(<EmptyState variant="error" title="x" onRetry={() => {}} retryLabel="Try again" />);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
