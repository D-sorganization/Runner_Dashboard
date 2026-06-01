// @vitest-environment jsdom
/**
 * Behaviour tests for the GitHub-style left Sidebar (issue #798, part of #796).
 *
 * Contract:
 *  - renders one section per nav group, with the group label as a heading;
 *  - renders every registry item as a nav button that calls onSelect(tabId);
 *  - highlights the active item (aria-current="page");
 *  - groups are collapsible; collapsed state persists to localStorage;
 *  - the whole sidebar is collapsible to an icon rail (persisted);
 *  - keyboard: ArrowUp/Down move focus between items; aria roles present;
 *  - every nav item carries an accessible name (label) and a tooltip/title.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "../Sidebar";
import { NAV_GROUPS, NAV_ITEMS } from "../navRegistry";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
beforeEach(() => {
  window.localStorage.clear();
});

function renderSidebar(props: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  const onSelect = props.onSelect ?? vi.fn();
  render(<Sidebar activeTabId={props.activeTabId ?? "overview"} onSelect={onSelect} {...props} />);
  return { onSelect };
}

describe("Sidebar — structure", () => {
  it("renders a complementary/navigation landmark", () => {
    renderSidebar();
    expect(screen.getByRole("navigation", { name: /sections/i })).toBeInTheDocument();
  });

  it("renders every declared group as a labelled section", () => {
    renderSidebar();
    for (const g of NAV_GROUPS) {
      expect(screen.getByRole("button", { name: g.label })).toBeInTheDocument();
    }
  });

  it("renders a nav button for every registry item", () => {
    renderSidebar();
    // Query the nav-item buttons once (O(n)) rather than running an accessible-name
    // regex scan per registry entry (O(n^2)) — the latter is correct but slow enough
    // under coverage instrumentation to blow the per-test timeout.
    const nav = screen.getByRole("navigation", { name: /sections/i });
    const navButtons = within(nav)
      .getAllByRole("button")
      .filter((b) => b.getAttribute("data-nav-item") === "true");
    const renderedLabels = navButtons.map((b) => b.textContent?.trim());
    // One nav button per registry item, with a matching accessible label.
    expect(navButtons).toHaveLength(NAV_ITEMS.length);
    for (const item of NAV_ITEMS) {
      expect(renderedLabels).toContain(item.label);
    }
  });

  it("gives every nav item an accessible title/tooltip", () => {
    renderSidebar();
    const nav = screen.getByRole("navigation", { name: /sections/i });
    const navButtons = within(nav)
      .getAllByRole("button")
      .filter((b) => b.getAttribute("data-nav-item") === "true");
    // Map label -> button so we can assert each registry item's tooltip in O(n).
    const byLabel = new Map(navButtons.map((b) => [b.textContent?.trim(), b]));
    for (const item of NAV_ITEMS) {
      const btn = byLabel.get(item.label);
      expect(btn).toBeDefined();
      expect(btn).toHaveAttribute("title", item.tooltip);
      // Tooltip must be a non-empty accessible hint (contract).
      expect(item.tooltip.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("Sidebar — selection & active state", () => {
  it("calls onSelect with the item's tabId on click", () => {
    const { onSelect } = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: /^Queue$/i }));
    expect(onSelect).toHaveBeenCalledWith("queue");
  });

  it("marks the active item with aria-current", () => {
    renderSidebar({ activeTabId: "remediation" });
    const active = screen.getByRole("button", { name: /^Remediation$/i });
    expect(active).toHaveAttribute("aria-current", "page");
    const inactive = screen.getByRole("button", { name: /^Queue$/i });
    expect(inactive).not.toHaveAttribute("aria-current", "page");
  });
});

describe("Sidebar — collapsible groups (persisted)", () => {
  it("collapses a group on header click and persists to localStorage", () => {
    renderSidebar();
    const firstGroup = NAV_GROUPS[0];
    const firstItemInGroup = NAV_ITEMS.find((i) => i.group === firstGroup.id)!;
    const header = screen.getByRole("button", { name: firstGroup.label });
    // Initially expanded — item visible.
    expect(screen.getByRole("button", { name: new RegExp(`^${firstItemInGroup.label}$`, "i") })).toBeVisible();
    expect(header).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    // Item hidden from the tree now.
    expect(
      screen.queryByRole("button", { name: new RegExp(`^${firstItemInGroup.label}$`, "i") }),
    ).not.toBeInTheDocument();
    // Persisted.
    const raw = window.localStorage.getItem("dashboard.sidebar.collapsedGroups");
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string)).toContain(firstGroup.id);
  });

  it("restores collapsed groups from localStorage on mount", () => {
    const g = NAV_GROUPS[1];
    window.localStorage.setItem(
      "dashboard.sidebar.collapsedGroups",
      JSON.stringify([g.id]),
    );
    renderSidebar();
    const header = screen.getByRole("button", { name: g.label });
    expect(header).toHaveAttribute("aria-expanded", "false");
  });
});

describe("Sidebar — rail collapse (persisted)", () => {
  it("toggles the icon-rail collapse and persists it", () => {
    renderSidebar();
    const toggle = screen.getByRole("button", { name: /collapse sidebar|expand sidebar/i });
    fireEvent.click(toggle);
    expect(window.localStorage.getItem("dashboard.sidebar.railCollapsed")).toBe("true");
  });
});

describe("Sidebar — keyboard navigation", () => {
  it("moves focus between items with ArrowDown / ArrowUp", () => {
    renderSidebar();
    const nav = screen.getByRole("navigation", { name: /sections/i });
    const items = within(nav).getAllByRole("button").filter((b) => b.getAttribute("data-nav-item") === "true");
    items[0].focus();
    expect(items[0]).toHaveFocus();
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(items[1]).toHaveFocus();
    fireEvent.keyDown(items[1], { key: "ArrowUp" });
    expect(items[0]).toHaveFocus();
  });
});
