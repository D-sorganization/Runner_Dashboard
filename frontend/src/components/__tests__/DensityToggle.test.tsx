// @vitest-environment jsdom
/**
 * Behaviour tests for components/DensityToggle.tsx — the compact /
 * comfortable table-density switch rendered in the desktop shell header.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DensityToggle } from "../DensityToggle";

afterEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.removeAttribute("data-density");
});

describe("DensityToggle", () => {
  it("renders through the touch button primitive", () => {
    render(<DensityToggle />);

    const toggle = screen.getByRole("button", { name: "Comfortable" });
    expect(toggle).toHaveAttribute("data-touch-primitive", "TouchButton");
    expect(toggle).toHaveClass("density-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("toggles compact density and updates the root attribute", () => {
    render(<DensityToggle />);

    fireEvent.click(screen.getByRole("button", { name: "Comfortable" }));

    const toggle = screen.getByRole("button", { name: "Compact" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement).toHaveAttribute("data-density", "compact");
    expect(localStorage.getItem("runner-dashboard:density")).toBe("compact");
  });
});
