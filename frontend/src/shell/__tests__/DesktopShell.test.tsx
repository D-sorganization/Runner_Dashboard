// @vitest-environment jsdom
/**
 * Behaviour tests for the modern desktop shell (issue #802, part of #796).
 *
 * Contract:
 *  - renders the left Sidebar (navigation landmark) AND the slim TopToolstrip
 *    (primary toolbar), both driven by the shared nav registry;
 *  - renders the page body (children) in a main landmark;
 *  - selecting a category in either nav surface calls onSelect(tabId);
 *  - the active category is reflected in both nav surfaces;
 *  - every action button is rendered with an accessible name and a tooltip;
 *  - A11Y AUDIT: no interactive control (button/link) in the whole shell lacks
 *    an accessible label.
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
import { DesktopShell, type ShellAction } from "../DesktopShell";

afterEach(cleanup);

function actions(spy = vi.fn()): ShellAction[] {
  return [
    { id: "refresh", label: "Refresh", tooltip: "Reload all dashboard data.", onClick: () => spy("refresh") },
    { id: "chat", label: "Chat", tooltip: "Toggle the assistant chat sidebar.", onClick: () => spy("chat") },
  ];
}

function renderShell(props: Partial<React.ComponentProps<typeof DesktopShell>> = {}) {
  const onSelect = props.onSelect ?? vi.fn();
  render(
    <DesktopShell
      activeTabId={props.activeTabId ?? "overview"}
      onSelect={onSelect}
      actions={props.actions ?? actions()}
      {...props}
    >
      <div data-testid="page-body">PAGE</div>
    </DesktopShell>,
  );
  return { onSelect };
}

describe("DesktopShell — structure", () => {
  it("renders the sidebar navigation landmark", () => {
    renderShell();
    expect(screen.getByRole("navigation", { name: /dashboard sections/i })).toBeInTheDocument();
  });

  it("renders the slim top toolstrip", () => {
    renderShell();
    expect(screen.getByRole("toolbar", { name: /primary navigation/i })).toBeInTheDocument();
  });

  it("renders the page body in a main landmark", () => {
    renderShell();
    const main = screen.getByRole("main");
    expect(main).toHaveClass("desktop-shell__main");
    expect(within(main).getByTestId("page-body")).toBeInTheDocument();
  });
});

describe("DesktopShell — navigation", () => {
  it("selecting a frequent toolstrip button calls onSelect(tabId)", () => {
    const { onSelect } = renderShell();
    const bar = screen.getByRole("toolbar", { name: /primary navigation/i });
    fireEvent.click(within(bar).getByRole("button", { name: /^Remediation$/i }));
    expect(onSelect).toHaveBeenCalledWith("remediation");
  });

  it("selecting a sidebar item calls onSelect(tabId)", () => {
    const { onSelect } = renderShell();
    const nav = screen.getByRole("navigation", { name: /dashboard sections/i });
    fireEvent.click(within(nav).getByRole("button", { name: /^Settings$/i }));
    expect(onSelect).toHaveBeenCalledWith("settings");
  });

  it("reflects the active category in the sidebar", () => {
    renderShell({ activeTabId: "queue" });
    const nav = screen.getByRole("navigation", { name: /dashboard sections/i });
    expect(within(nav).getByRole("button", { name: /^Queue$/i })).toHaveAttribute("aria-current", "page");
  });
});

describe("DesktopShell — action buttons", () => {
  it("renders each action with its accessible name", () => {
    renderShell();
    expect(screen.getByRole("button", { name: /^Refresh$/i })).toHaveClass("shell-action");
    expect(screen.getByRole("button", { name: /^Chat$/i })).toHaveClass("shell-action");
  });

  it("marks active actions with a scoped class", () => {
    renderShell({
      actions: [
        {
          id: "chat",
          label: "Chat",
          tooltip: "Toggle the assistant chat sidebar.",
          onClick: vi.fn(),
          active: true,
        },
      ],
    });
    expect(screen.getByRole("button", { name: /^Chat$/i })).toHaveClass("shell-action--active");
  });

  it("fires the action onClick", () => {
    const spy = vi.fn();
    renderShell({ actions: actions(spy) });
    fireEvent.click(screen.getByRole("button", { name: /^Refresh$/i }));
    expect(spy).toHaveBeenCalledWith("refresh");
  });

  it("exposes the action tooltip via aria-describedby on focus", async () => {
    renderShell();
    const btn = screen.getByRole("button", { name: /^Refresh$/i });
    fireEvent.focus(btn);
    const tip = await screen.findByRole("tooltip");
    expect(btn).toHaveAttribute("aria-describedby", tip.id);
  });
});

describe("DesktopShell — accessibility audit", () => {
  it("no interactive control lacks an accessible label", () => {
    const { container } = render(
      <DesktopShell activeTabId="overview" onSelect={vi.fn()} actions={actions()}>
        <div>PAGE</div>
      </DesktopShell>,
    );
    const controls = Array.from(
      container.querySelectorAll<HTMLElement>("button, a[href], [role='button'], [role='menuitem']"),
    );
    expect(controls.length).toBeGreaterThan(0);
    const unlabelled = controls.filter((el) => {
      const aria = el.getAttribute("aria-label");
      if (aria && aria.trim()) return false;
      const text = (el.textContent ?? "").trim();
      if (text) return false;
      const title = el.getAttribute("title");
      if (title && title.trim()) return false;
      return true;
    });
    expect(unlabelled).toEqual([]);
  });
});
