// @vitest-environment jsdom
/**
 * The consolidated Settings area (#1338, SC-G6 owner decisions): one page with
 * anchored sections. Local Tools is one of those sections, not its own tab.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

vi.mock("../../../components/ThemeSettings", () => ({
  ThemeSettings: () => <div data-testid="theme-settings">Theme</div>,
}));

vi.mock("../../LocalApps", () => ({
  LocalAppsPage: () => <div data-testid="local-apps-page">Local apps</div>,
}));

import { SettingsPage } from "../SettingsPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

describe("SettingsPage (#1338)", () => {
  it("renders the theme and Local Tools sections with anchors", () => {
    const { container } = render(<SettingsPage />);

    const theme = container.querySelector("section#theme");
    const localTools = container.querySelector("section#local-tools");
    expect(theme).not.toBeNull();
    expect(localTools).not.toBeNull();
    expect(within(theme as HTMLElement).getByTestId("theme-settings")).toBeInTheDocument();
    expect(
      within(localTools as HTMLElement).getByTestId("local-apps-page"),
    ).toBeInTheDocument();
  });

  it("names every section for assistive tech", () => {
    render(<SettingsPage />);

    expect(screen.getByRole("region", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Local Tools" })).toBeInTheDocument();
  });

  it("offers a jump link per section", () => {
    render(<SettingsPage />);

    const nav = screen.getByRole("navigation", { name: /settings sections/i });
    expect(within(nav).getByRole("link", { name: "Theme" })).toHaveAttribute(
      "href",
      "#theme",
    );
    expect(within(nav).getByRole("link", { name: "Local Tools" })).toHaveAttribute(
      "href",
      "#local-tools",
    );
  });

  it("scrolls to the section named by the URL hash on mount", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    window.location.hash = "#local-tools";

    render(<SettingsPage />);

    expect(scrollIntoView).toHaveBeenCalled();
  });
});
