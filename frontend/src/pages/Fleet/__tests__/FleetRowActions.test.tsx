// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FleetRowActions } from "../FleetRowActions";

describe("FleetRowActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders trigger button with accessible attributes", () => {
    render(
      <FleetRowActions
        target="runner-worker-1"
        isMachine={false}
        onSelectAction={vi.fn()}
      />
    );

    const trigger = screen.getByRole("button", { name: /actions/i });
    expect(trigger).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("opens menu on click and lists all 5 required actions", () => {
    render(
      <FleetRowActions
        target="runner-worker-1"
        isMachine={false}
        onSelectAction={vi.fn()}
      />
    );

    const trigger = screen.getByRole("button", { name: /actions/i });
    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu")).toBeInTheDocument();

    const expectedActions = [
      "Bring online",
      "Take offline",
      "Restart",
      "Compact disk",
      "Diagnose",
    ];

    for (const label of expectedActions) {
      expect(screen.getByRole("menuitem", { name: label })).toBeInTheDocument();
    }
  });

  it("fires onSelectAction with the correct action key when clicked", () => {
    const onSelectAction = vi.fn();
    render(
      <FleetRowActions
        target="runner-worker-1"
        isMachine={false}
        onSelectAction={onSelectAction}
      />
    );

    const trigger = screen.getByRole("button", { name: /actions/i });
    fireEvent.click(trigger);

    const takeOfflineItem = screen.getByRole("menuitem", { name: "Take offline" });
    fireEvent.click(takeOfflineItem);

    expect(onSelectAction).toHaveBeenCalledTimes(1);
    expect(onSelectAction).toHaveBeenCalledWith("take_offline");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes the menu on Escape key press", () => {
    render(
      <FleetRowActions
        target="runner-worker-1"
        isMachine={false}
        onSelectAction={vi.fn()}
      />
    );

    const trigger = screen.getByRole("button", { name: /actions/i });
    fireEvent.click(trigger);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
