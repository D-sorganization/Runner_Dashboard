// @vitest-environment jsdom
/** The mobile FAB must sit above the bottom navigation bar (#1718 live sweep). */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FloatingActionButton } from "../FloatingActionButton";

afterEach(cleanup);

describe("FloatingActionButton", () => {
  it("clears the bottom navigation bar instead of overlapping it", () => {
    render(<FloatingActionButton aria-label="Quick action" onClick={() => {}} />);
    const style = screen.getByRole("button", { name: "Quick action" }).getAttribute("style") || "";
    expect(style).toContain("var(--bottom-nav-height");
    expect(style).toContain("safe-area-inset-bottom");
  });
});
