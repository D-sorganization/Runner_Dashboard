// @vitest-environment jsdom
/**
 * Behaviour tests for the slim top toolstrip (issue #799, part of #796).
 *
 * Contract:
 *  - renders ONLY the registry's `frequent` items as direct toolstrip buttons;
 *  - non-frequent items are NOT direct buttons — they live behind a "More" menu;
 *  - clicking a frequent button calls onSelect(tabId);
 *  - the active item is marked aria-current="page";
 *  - a "More" dropdown exposes the remaining categories and selecting one calls
 *    onSelect(tabId);
 *  - if the active tab is a non-frequent category, the "More" trigger reflects
 *    that selection (aria-current) so the user can see where they are;
 *  - every direct button has an accessible name and a tooltip (aria-describedby
 *    on focus);
 *  - the toolbar is a labelled WAI-ARIA toolbar that wraps responsively.
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
import { afterEach, describe, expect, it, vi } from "vitest";
import { TopToolstrip } from "../TopToolstrip";
import { NAV_ITEMS, frequentItems } from "../navRegistry";

afterEach(cleanup);

function renderBar(props: Partial<React.ComponentProps<typeof TopToolstrip>> = {}) {
  const onSelect = props.onSelect ?? vi.fn();
  render(
    <TopToolstrip
      activeTabId={props.activeTabId ?? "overview"}
      onSelect={onSelect}
      {...props}
    />,
  );
  return { onSelect };
}

describe("TopToolstrip — frequent items only", () => {
  it("renders a direct button for every frequent item", () => {
    renderBar();
    const bar = screen.getByRole("toolbar", { name: /primary/i });
    for (const item of frequentItems()) {
      expect(
        within(bar).getByRole("button", {
          name: new RegExp(`^${item.label}$`, "i"),
        }),
      ).toBeInTheDocument();
    }
  });

  it("does NOT render non-frequent items as direct toolbar buttons", () => {
    renderBar();
    const bar = screen.getByRole("toolbar", { name: /primary/i });
    const nonFreq = NAV_ITEMS.filter((i) => !i.frequent);
    for (const item of nonFreq) {
      expect(
        within(bar).queryByRole("button", {
          name: new RegExp(`^${item.label}$`, "i"),
        }),
      ).not.toBeInTheDocument();
    }
  });

  it("renders fewer direct buttons than the full registry (it is slim)", () => {
    renderBar();
    expect(frequentItems().length).toBeLessThan(NAV_ITEMS.length);
  });

  it("calls onSelect with tabId when a frequent button is clicked", () => {
    const { onSelect } = renderBar();
    fireEvent.click(screen.getByRole("button", { name: /^Remediation$/i }));
    expect(onSelect).toHaveBeenCalledWith("remediation");
  });

  it("marks the active frequent item with aria-current", () => {
    renderBar({ activeTabId: "queue" });
    expect(
      screen.getByRole("button", { name: /^Queue$/i }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: /^Fleet$/i }),
    ).not.toHaveAttribute("aria-current", "page");
  });

  it("gives each frequent button an accessible tooltip via aria-describedby on focus", async () => {
    renderBar();
    const btn = screen.getByRole("button", { name: /^Fleet$/i });
    fireEvent.focus(btn);
    const tip = await screen.findByRole("tooltip");
    expect(btn).toHaveAttribute("aria-describedby", tip.id);
  });
});

describe("TopToolstrip — overflow menu", () => {
  it("exposes a More menu containing the non-frequent categories", () => {
    renderBar();
    const more = screen.getByRole("button", { name: /more/i });
    fireEvent.click(more);
    const menu = screen.getByRole("menu");
    expect(
      within(menu).getByRole("menuitem", { name: /machines/i }),
    ).toBeInTheDocument();
  });

  it("the More menu contains EVERY non-frequent category", () => {
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    const menu = screen.getByRole("menu");
    const nonFreq = NAV_ITEMS.filter((i) => !i.frequent);
    for (const item of nonFreq) {
      expect(
        within(menu).getByRole("menuitem", {
          name: new RegExp(`^${item.label}$`, "i"),
        }),
      ).toBeInTheDocument();
    }
  });

  it("selecting a category from the More menu calls onSelect(tabId)", () => {
    const { onSelect } = renderBar();
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /machines/i }));
    expect(onSelect).toHaveBeenCalledWith("machines");
  });

  it("marks the More trigger active when the current tab is a non-frequent category", () => {
    renderBar({ activeTabId: "machines" });
    const more = screen.getByRole("button", { name: /more/i });
    expect(more).toHaveAttribute("aria-current", "page");
  });

  it("does NOT mark the More trigger active when a frequent tab is current", () => {
    renderBar({ activeTabId: "overview" });
    const more = screen.getByRole("button", { name: /more/i });
    expect(more).not.toHaveAttribute("aria-current", "page");
  });
});

describe("TopToolstrip — responsive container", () => {
  it("is a labelled toolbar that wraps", () => {
    renderBar();
    const bar = screen.getByRole("toolbar", { name: /primary/i });
    expect(bar).toHaveClass("slim-toolstrip");
    expect(bar.style.flexWrap).toBe("wrap");
  });
});
