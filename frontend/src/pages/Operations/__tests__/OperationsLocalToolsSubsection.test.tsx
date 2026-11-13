// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

vi.mock("../../LocalApps", () => ({
  LocalAppsPage: () => <div data-testid="local-apps-page">Local tools</div>,
}));

import { OperationsLocalToolsSubsection } from "../OperationsLocalToolsSubsection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

describe("OperationsLocalToolsSubsection (#1338)", () => {
  it("is collapsed by default", () => {
    render(<OperationsLocalToolsSubsection />);

    expect(screen.getByRole("region", { name: "Local Tools" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /show local tools/i })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByTestId("local-apps-page")).not.toBeInTheDocument();
  });

  it("opens when expanded or when hash is #local-tools", () => {
    render(<OperationsLocalToolsSubsection />);
    fireEvent.click(screen.getByRole("button", { name: /show local tools/i }));
    expect(screen.getByTestId("local-apps-page")).toBeInTheDocument();

    cleanup();
    window.location.hash = "#local-tools";
    render(<OperationsLocalToolsSubsection />);
    expect(screen.getByTestId("local-apps-page")).toBeInTheDocument();
  });
});
