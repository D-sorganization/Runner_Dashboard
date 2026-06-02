// @vitest-environment jsdom
/**
 * Tests for IntroHeader primitive (#822).
 *
 * TDD: authored alongside the primitive. Covers content, the accessible
 * region name, and the optional dismiss affordance.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { IntroHeader } from "../IntroHeader";

afterEach(cleanup);

describe("IntroHeader", () => {
  it("renders the body and an accessible region named after the tab", () => {
    render(<IntroHeader title="Queue" body="Queued and in-progress runs." />);
    expect(screen.getByText("Queued and in-progress runs.")).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: /about queue/i }),
    ).toBeInTheDocument();
  });

  it("omits the dismiss button when no handler is provided", () => {
    render(<IntroHeader title="Queue" body="x" />);
    expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
  });

  it("fires onDismiss when the dismiss button is clicked", () => {
    const onDismiss = vi.fn();
    render(<IntroHeader title="Queue" body="x" onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: /dismiss queue intro/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
