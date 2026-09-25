// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FleetRunnersSection } from "../FleetRunnersSection";

afterEach(() => {
  cleanup();
});

const mockRunners = [
  {
    id: 1,
    name: "d-sorg-local-ControlTower-1",
    status: "online",
    busy: false,
    labels: [{ name: "self-hosted" }, { name: "linux" }],
  },
  {
    id: 2,
    name: "d-sorg-local-ControlTower-2",
    status: "online",
    busy: true,
    labels: [{ name: "self-hosted" }],
  },
  {
    id: 3,
    name: "d-sorg-local-DeskComputer-1",
    status: "offline",
    busy: false,
    labels: [],
  },
];

const mockRuns = [
  {
    id: 991,
    run_number: 42,
    runner_name: "d-sorg-local-ControlTower-2",
    head_branch: "feat/some-branch",
    workflow_name: "CI Standard",
    html_url: "https://github.com/runs/991",
  },
];

describe("FleetRunnersSection", () => {
  it("renders runner counts and status filter pills", () => {
    render(
      <FleetRunnersSection
        runners={mockRunners}
        runs={mockRuns}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onFleet={vi.fn()}
        onRunner={vi.fn()}
      />
    );

    expect(screen.getByRole("region", { name: /Runner Fleet/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^All \(/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Online \(/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Busy \(/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Offline \(/i })).toBeInTheDocument();
  });

  it("filters runners by status when filter pill is clicked", () => {
    render(
      <FleetRunnersSection
        runners={mockRunners}
        runs={mockRuns}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onFleet={vi.fn()}
        onRunner={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /^Offline \(/i }));

    expect(screen.getByText("d-sorg-local-DeskComputer-1")).toBeInTheDocument();
    expect(screen.queryByText("d-sorg-local-ControlTower-1")).not.toBeInTheDocument();
  });

  it("dispatches fleet controls and runner actions", () => {
    const onFleet = vi.fn();
    const onRunner = vi.fn();
    render(
      <FleetRunnersSection
        runners={mockRunners}
        runs={mockRuns}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onFleet={onFleet}
        onRunner={onRunner}
      />
    );

    const startAll = screen.getByRole("button", { name: /Start All/i });
    fireEvent.click(startAll);
    expect(onFleet).toHaveBeenCalledWith("all-up");

    const stopBtn = screen.getAllByRole("button", { name: /^Stop$/i })[0];
    fireEvent.click(stopBtn);
    expect(onRunner).toHaveBeenCalledWith(1, "stop");
  });

  it("fails visibly with independent error state and retry CTA", () => {
    const onRetry = vi.fn();
    render(
      <FleetRunnersSection
        runners={[]}
        runs={[]}
        loading={false}
        error="Failed to load runners: HTTP 500"
        onRetry={onRetry}
        onFleet={vi.fn()}
        onRunner={vi.fn()}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load runners: HTTP 500/);
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });
});
