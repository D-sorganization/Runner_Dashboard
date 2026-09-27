// @vitest-environment jsdom
/**
 * The consolidated Settings area (#1338, SC-G6 owner decisions): one page with
 * anchored sections. Credentials, Notifications, Linear Setup, Principals,
 * Theme and Local Tools are sections, not tabs of their own.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

const breakpointMock = vi.fn(() => "lg");
vi.mock("../../../hooks/useBreakpoint", async (orig) => {
  const actual = await orig<typeof import("../../../hooks/useBreakpoint")>();
  return { ...actual, useBreakpoint: () => breakpointMock() };
});

vi.mock("../../CredentialsPage", () => ({
  CredentialsPage: () => <div data-testid="credentials-page">Credentials</div>,
}));

vi.mock("../../Credentials", () => ({
  CredentialsMobile: () => <div data-testid="credentials-mobile">Mobile credentials</div>,
}));

vi.mock("../../PushSettings", () => ({
  default: () => <div data-testid="push-settings">Push</div>,
}));

vi.mock("../../LinearSetup", () => ({
  LinearSetup: () => <div data-testid="linear-setup">Linear</div>,
}));

vi.mock("../../Principals", () => ({
  PrincipalsTab: () => <div data-testid="principals">Principals</div>,
}));

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
  breakpointMock.mockReturnValue("lg");
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

  it("renders every consolidated section in the owner's order", () => {
    const { container } = render(<SettingsPage />);

    const ids = Array.from(container.querySelectorAll("section")).map((s) => s.id);
    expect(ids).toEqual([
      "credentials",
      "linear-setup",
      "notifications",
      "principals",
      "theme",
      "local-tools",
    ]);
    const content: Record<string, string> = {
      credentials: "credentials-page",
      "linear-setup": "linear-setup",
      notifications: "push-settings",
      principals: "principals",
    };
    for (const [id, testId] of Object.entries(content)) {
      const section = container.querySelector(`section#${id}`) as HTMLElement;
      expect(within(section).getByTestId(testId)).toBeInTheDocument();
    }
  });

  it("uses the mobile credentials view on small screens", () => {
    breakpointMock.mockReturnValue("md");
    const { container } = render(<SettingsPage />);

    const section = container.querySelector("section#credentials") as HTMLElement;
    expect(within(section).getByTestId("credentials-mobile")).toBeInTheDocument();
    expect(within(section).queryByTestId("credentials-page")).not.toBeInTheDocument();
  });

  it("names every section for assistive tech", () => {
    render(<SettingsPage />);

    expect(screen.getByRole("region", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Local Tools" })).toBeInTheDocument();
    for (const name of ["Credentials", "Linear Setup", "Notifications", "Principals"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
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
