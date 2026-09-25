// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsStatusBanner } from "../OperationsStatusBanner";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("OperationsStatusBanner", () => {
  it("renders title, orientation subtitle, and all 5 section jump buttons", () => {
    render(<OperationsStatusBanner />);

    expect(screen.getByRole("heading", { name: /Fleet Operations/i })).toBeInTheDocument();
    expect(
      screen.getByText(/Deployment rollouts, Conductor admission, runner hours, scheduled workflows, and diagnostics/i),
    ).toBeInTheDocument();

    expect(screen.getByRole("button", { name: /Deploy & versions/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Admission \(Conductor\)/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Runner hours/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Scheduled workflows/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Diagnostics/i })).toBeInTheDocument();
  });

  it("fires onJumpToSection callback when a section jump button is clicked", () => {
    const onJumpToSection = vi.fn();
    render(<OperationsStatusBanner onJumpToSection={onJumpToSection} />);

    fireEvent.click(screen.getByRole("button", { name: /Admission \(Conductor\)/i }));
    expect(onJumpToSection).toHaveBeenCalledWith("admission");

    fireEvent.click(screen.getByRole("button", { name: /Runner hours/i }));
    expect(onJumpToSection).toHaveBeenCalledWith("runner-hours");

    fireEvent.click(screen.getByRole("button", { name: /Scheduled workflows/i }));
    expect(onJumpToSection).toHaveBeenCalledWith("scheduled-workflows");

    fireEvent.click(screen.getByRole("button", { name: /Diagnostics/i }));
    expect(onJumpToSection).toHaveBeenCalledWith("diagnostics");

    fireEvent.click(screen.getByRole("button", { name: /Deploy & versions/i }));
    expect(onJumpToSection).toHaveBeenCalledWith("deploy");
  });

  it("displays metric summary values when provided", () => {
    render(
      <OperationsStatusBanner
        deploySummary={{
          expectedVersion: "4.10.0",
          rolloutStatus: "steady",
          driftCount: 0,
        }}
        admissionSummary={{
          mode: "running",
          activeLeases: 4,
          workPlanned: 12,
        }}
        runnerHoursSummary={{
          desiredRunners: 8,
          onlineRunners: 8,
        }}
        scheduledWorkflowsSummary={{
          totalCount: 15,
        }}
        diagnosticsSummary={{
          gitDrift: false,
          memoryMb: 128,
        }}
      />,
    );

    expect(screen.getByText("4.10.0")).toBeInTheDocument();
    expect(screen.getByText("steady")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("8/8 online")).toBeInTheDocument();
    expect(screen.getByText("15 workflows")).toBeInTheDocument();
    expect(screen.getByText("128 MB")).toBeInTheDocument();
  });
});
