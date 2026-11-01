/**
 * The FAB floats above the scroll area (#1718), but the scroll area itself
 * never reserved room for it, so the last Fleet/Queue card sat underneath
 * the FAB with no way to tap it (#3751). The content area now adds FAB
 * clearance on exactly the tabs where the FAB is visible.
 */
import React from "react";
import { describe, it, expect, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MobileShell } from "../MobileShell";

const breakpointMock = vi.hoisted(() => ({ value: "md" }));

vi.mock("../../hooks/useBreakpoint", () => ({
  useBreakpoint: () => breakpointMock.value,
}));

afterEach(() => {
  cleanup();
});

describe("MobileShell — content FAB clearance (#3751)", () => {
  it("adds FAB clearance on a tab where the Ask FAB is visible (queue)", () => {
    render(
      <MobileShell currentTab="queue" onTabChange={vi.fn()}>
        <div>Queue page</div>
      </MobileShell>,
    );
    expect(screen.getByRole("main")).toHaveClass("mobile-shell__content--fab-clearance");
  });

  it("adds FAB clearance on a tab where the Ask FAB is visible (overview/Fleet)", () => {
    render(
      <MobileShell currentTab="overview" onTabChange={vi.fn()}>
        <div>Fleet page</div>
      </MobileShell>,
    );
    expect(screen.getByRole("main")).toHaveClass("mobile-shell__content--fab-clearance");
  });

  it("omits FAB clearance on a tab with no FAB (staff)", () => {
    render(
      <MobileShell currentTab="staff" onTabChange={vi.fn()}>
        <div>Staff page</div>
      </MobileShell>,
    );
    expect(screen.getByRole("main")).not.toHaveClass("mobile-shell__content--fab-clearance");
  });
});
