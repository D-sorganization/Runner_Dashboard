// @vitest-environment jsdom
/**
 * Behaviour tests for the Dropdown menu primitive (issue #800, part of #796).
 *
 * Contract:
 *  - a trigger button with aria-haspopup="menu" and aria-expanded reflecting state;
 *  - opens on click; menu has role="menu" and items role="menuitem";
 *  - keyboard: ArrowDown/ArrowUp move focus, Enter/Space activate, Escape closes;
 *  - click-outside closes;
 *  - selecting an item fires its onSelect and closes the menu;
 *  - focus returns to the trigger on close.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dropdown, type DropdownItem } from "../Dropdown";

afterEach(cleanup);

function items(onSelect = vi.fn()): { items: DropdownItem[]; onSelect: ReturnType<typeof vi.fn> } {
  return {
    onSelect,
    items: [
      { id: "a", label: "Alpha", onSelect: () => onSelect("a") },
      { id: "b", label: "Beta", onSelect: () => onSelect("b") },
      { id: "c", label: "Gamma", onSelect: () => onSelect("c") },
    ],
  };
}

describe("Dropdown", () => {
  it("renders a trigger with aria-haspopup and collapsed aria-expanded", () => {
    const { items: its } = items();
    render(<Dropdown label="Menu" items={its} />);
    const trigger = screen.getByRole("button", { name: /menu/i });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("is closed initially", () => {
    const { items: its } = items();
    render(<Dropdown label="Menu" items={its} />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("opens on click and exposes role=menu with menuitems", () => {
    const { items: its } = items();
    render(<Dropdown label="Menu" items={its} />);
    fireEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
    expect(screen.getByRole("button", { name: /menu/i })).toHaveAttribute("aria-expanded", "true");
  });

  it("selects an item, fires onSelect, and closes", async () => {
    const onSelect = vi.fn();
    const { items: its } = items(onSelect);
    render(<Dropdown label="Menu" items={its} />);
    fireEvent.click(screen.getByRole("button", { name: /menu/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Beta" }));
    expect(onSelect).toHaveBeenCalledWith("b");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("closes on Escape and restores focus to the trigger", async () => {
    const { items: its } = items();
    render(<Dropdown label="Menu" items={its} />);
    const trigger = screen.getByRole("button", { name: /menu/i });
    fireEvent.click(trigger);
    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("moves focus with ArrowDown / ArrowUp", () => {
    const { items: its } = items();
    render(<Dropdown label="Menu" items={its} />);
    fireEvent.click(screen.getByRole("button", { name: /menu/i }));
    const menu = screen.getByRole("menu");
    const menuitems = screen.getAllByRole("menuitem");
    // First item focused on open.
    expect(menuitems[0]).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(menuitems[1]).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(menuitems[0]).toHaveFocus();
    // Wraps from first up to last.
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(menuitems[2]).toHaveFocus();
  });

  it("activates the focused item with Enter", async () => {
    const onSelect = vi.fn();
    const { items: its } = items(onSelect);
    render(<Dropdown label="Menu" items={its} />);
    fireEvent.click(screen.getByRole("button", { name: /menu/i }));
    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "ArrowDown" }); // focus Beta
    fireEvent.keyDown(menu, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("b");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("closes on outside click", async () => {
    const { items: its } = items();
    render(
      <div>
        <Dropdown label="Menu" items={its} />
        <button type="button">outside</button>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("button", { name: "outside" }));
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });
});
