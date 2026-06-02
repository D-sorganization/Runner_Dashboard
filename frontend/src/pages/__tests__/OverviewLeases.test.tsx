// @vitest-environment jsdom
/**
 * Behaviour test for pages/OverviewLeases.tsx — the static "Fair Sharing &
 * Active Leases" (Wave 3) preview panel extracted from the legacy App.tsx
 * monolith (decomposition #836, pass 12).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { OverviewLeases } from "../OverviewLeases";

afterEach(cleanup);

describe("OverviewLeases", () => {
  it("renders the Wave 3 lease preview cards", () => {
    render(<OverviewLeases />);
    expect(screen.getByText("Fair Sharing & Active Leases")).toBeInTheDocument();
    expect(screen.getByText("Wave 3")).toBeInTheDocument();
    expect(screen.getByText("USER: dieterolson")).toBeInTheDocument();
    expect(screen.getByText("USER: jules-bot")).toBeInTheDocument();
    expect(screen.getByText("ubuntu-latest-4xlarge")).toBeInTheDocument();
    expect(screen.getByText("windows-2022-standard")).toBeInTheDocument();
  });

  it("exposes the relinquish + view-logs action buttons", () => {
    render(<OverviewLeases />);
    expect(
      screen.getByRole("button", { name: "Relinquish runner" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "View runner logs" }),
    ).toBeInTheDocument();
  });
});
