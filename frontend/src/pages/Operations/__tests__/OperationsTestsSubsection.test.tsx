// @vitest-environment jsdom
/**
 * Tests lives under Operations → Diagnostics (#1338, owner decision). The
 * subsection is collapsed by default so opening Operations does not fetch the
 * CI and heavy-test inventories; it opens on request or when the URL hash is
 * #tests (the redirect target for the retired /settings/tests route).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";

vi.mock("../../TestsPage", () => ({
  TestsPage: () => <div data-testid="tests-page">Tests page</div>,
}));

import { OperationsTestsSubsection } from "../OperationsTestsSubsection";
import { OperationsDiagnosticsSection } from "../OperationsDiagnosticsSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

describe("OperationsTestsSubsection (#1338)", () => {
  it("is an anchored subsection, collapsed by default", () => {
    const { container } = render(<OperationsTestsSubsection />);

    expect(container.querySelector("#tests")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Tests" })).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /show tests/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("tests-page")).not.toBeInTheDocument();
  });

  it("renders the Tests page when expanded and hides it again", () => {
    render(<OperationsTestsSubsection />);

    fireEvent.click(screen.getByRole("button", { name: /show tests/i }));
    expect(screen.getByTestId("tests-page")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /hide tests/i });
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(toggle);
    expect(screen.queryByTestId("tests-page")).not.toBeInTheDocument();
  });

  it("opens itself when the URL hash is #tests", () => {
    window.location.hash = "#tests";
    render(<OperationsTestsSubsection />);

    expect(screen.getByTestId("tests-page")).toBeInTheDocument();
  });

  it("opens when the hash changes to #tests after mount", () => {
    render(<OperationsTestsSubsection />);
    expect(screen.queryByTestId("tests-page")).not.toBeInTheDocument();

    window.location.hash = "#tests";
    fireEvent(window, new HashChangeEvent("hashchange"));

    expect(screen.getByTestId("tests-page")).toBeInTheDocument();
  });

  it("is part of the Diagnostics section", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const { container } = render(<OperationsDiagnosticsSection />);

    const diagnostics = container.querySelector("section#diagnostics");
    expect(diagnostics).not.toBeNull();
    expect(diagnostics?.querySelector("#tests")).not.toBeNull();
  });
});
