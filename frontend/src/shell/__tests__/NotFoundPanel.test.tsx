// @vitest-environment jsdom
/**
 * Tests for NotFoundPanel (issue #1309 / SC-D2).
 *
 * Contract:
 *  - Renders inside the shell with a visible not-found panel (never blank);
 *  - Displays the attempted route so the user knows what failed;
 *  - Provides a classified error tag (NOT_FOUND / 404);
 *  - Provides a clear call-to-action button to return to Staff Console ("/").
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotFoundPanel } from "../NotFoundPanel";

afterEach(cleanup);

describe("NotFoundPanel", () => {
  it("renders the not-found heading and classified 404 badge", () => {
    render(<NotFoundPanel path="/unknown/route" onNavigateHome={vi.fn()} />);
    expect(screen.getByRole("region", { name: /route not found/i })).toBeInTheDocument();
    expect(screen.getByText(/404/i)).toBeInTheDocument();
    expect(screen.getByText(/page not found/i)).toBeInTheDocument();
  });

  it("displays the attempted pathname so the user sees what was requested", () => {
    render(<NotFoundPanel path="/some/missing/page" onNavigateHome={vi.fn()} />);
    expect(screen.getByText("/some/missing/page")).toBeInTheDocument();
  });

  it("gives recovery hints that also make sense on a phone (#1718)", () => {
    render(<NotFoundPanel path="/bad-link" onNavigateHome={vi.fn()} />);
    const region = screen.getByRole("region", { name: /route not found/i });
    expect(region.textContent).not.toMatch(/sidebar/i);
    expect(region.textContent).toMatch(/navigation/i);
    // The keyboard shortcut hint is desktop-only; CSS hides it on narrow screens.
    expect(screen.getByText(/Ctrl\+K/).closest(".not-found__shortcut")).not.toBeNull();
  });

  it("calls onNavigateHome when clicking the home action", () => {
    const onHome = vi.fn();
    render(<NotFoundPanel path="/bad-link" onNavigateHome={onHome} />);
    const btn = screen.getByRole("button", { name: /staff console/i });
    fireEvent.click(btn);
    expect(onHome).toHaveBeenCalledOnce();
  });
});
