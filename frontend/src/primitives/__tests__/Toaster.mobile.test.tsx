// @vitest-environment jsdom
/**
 * On mobile, a bottom-anchored toast sits right over the sticky Staff
 * composer hint and the Fleet/Queue FAB (#3751). The toast becomes a top
 * banner below the safe area instead, clear of any bottom chrome.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Toaster, useToast } from "../Toaster";

const breakpointMock = vi.hoisted(() => ({ value: "lg" }));

vi.mock("../../hooks/useBreakpoint", () => ({
  useBreakpoint: () => breakpointMock.value,
}));

afterEach(() => {
  cleanup();
  breakpointMock.value = "lg";
});

function Trigger() {
  const { showToast } = useToast();
  return (
    <button type="button" onClick={() => showToast("A dashboard update is ready.")}>
      fire
    </button>
  );
}

describe("Toaster — mobile placement (#3751)", () => {
  it("anchors to the bottom (desktop default) on a desktop breakpoint", () => {
    breakpointMock.value = "lg";
    render(
      <Toaster>
        <Trigger />
      </Toaster>,
    );
    act(() => screen.getByRole("button", { name: "fire" }).click());

    const region = screen.getByLabelText("Non-critical notifications").parentElement as HTMLElement;
    expect(region.style.bottom).not.toBe("");
    expect(region.style.top).toBe("");
  });

  it("anchors to the top (clear of the bottom nav, FAB and composer) on a mobile breakpoint", () => {
    breakpointMock.value = "sm";
    render(
      <Toaster>
        <Trigger />
      </Toaster>,
    );
    act(() => screen.getByRole("button", { name: "fire" }).click());

    const region = screen.getByLabelText("Non-critical notifications").parentElement as HTMLElement;
    expect(region.style.top).toContain("safe-area-inset-top");
    expect(region.style.bottom).toBe("auto");
  });
});
