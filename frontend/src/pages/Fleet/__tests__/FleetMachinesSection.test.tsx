// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FleetMachinesSection } from "../FleetMachinesSection";

afterEach(() => {
  cleanup();
});

const mockMachines = [
  {
    name: "ControlTower",
    online: true,
    dashboard_reachable: true,
    health: { runners_registered: 2 },
    system: {
      cpu: { percent: 18.5, percent_1m_avg: 15.2, count: 16 },
      memory: { percent: 45.0, used: 14400000000, total: 32000000000 },
      swap: { percent: 5.0 },
      storage: [
        { device: "/dev/nvme0n1p2", mountpoint: "/", percent: 62.0, total_bytes: 1000000000000, used_bytes: 620000000000 },
      ],
      os: { wsl_distro: "Ubuntu-24.04" },
    },
  },
  {
    name: "DeskComputer",
    online: false,
    dashboard_reachable: false,
    health: { runners_registered: 1 },
    offline_reason: "runner_service_offline",
    offline_detail: "No online runners or dashboard telemetry are visible.",
    system: {},
  },
];

const mockRunners = [
  { id: 1, name: "d-sorg-local-ControlTower-1", status: "online", busy: false, labels: ["self-hosted"] },
  { id: 2, name: "d-sorg-local-ControlTower-2", status: "online", busy: true, labels: ["self-hosted"] },
  { id: 3, name: "d-sorg-local-DeskComputer-1", status: "offline", busy: false, labels: [] },
];

describe("FleetMachinesSection", () => {
  it("renders machines table with summary and status badges", () => {
    render(
      <FleetMachinesSection
        machines={mockMachines}
        runners={mockRunners}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />
    );

    expect(screen.getByRole("region", { name: /Fleet Machines/i })).toBeInTheDocument();
    expect(screen.getByText("ControlTower")).toBeInTheDocument();
    expect(screen.getByText("DeskComputer")).toBeInTheDocument();
  });

  it("expands machine telemetry on row click", () => {
    render(
      <FleetMachinesSection
        machines={mockMachines}
        runners={mockRunners}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />
    );

    const expandBtn = screen.getByRole("button", { name: /expand ControlTower telemetry/i });
    fireEvent.click(expandBtn);

    expect(screen.getByText(/Storage Devices/i)).toBeInTheDocument();
    expect(screen.getByText(/\/dev\/nvme0n1p2/i)).toBeInTheDocument();
  });

  it("routes row actions through Maintenance role (SC-E6)", () => {
    const onAskMaintenance = vi.fn();
    render(
      <FleetMachinesSection
        machines={mockMachines}
        runners={mockRunners}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onAskMaintenance={onAskMaintenance}
      />
    );

    const maintBtns = screen.getAllByRole("button", { name: /Ask Maintenance/i });
    expect(maintBtns.length).toBeGreaterThan(0);
    fireEvent.click(maintBtns[0]);

    expect(onAskMaintenance).toHaveBeenCalledWith("ControlTower", expect.any(String));
  });

  it("fails visibly with independent error state and retry CTA", () => {
    const onRetry = vi.fn();
    render(
      <FleetMachinesSection
        machines={[]}
        runners={[]}
        loading={false}
        error="Failed to fetch /api/fleet/nodes: HTTP 500"
        onRetry={onRetry}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to fetch \/api\/fleet\/nodes: HTTP 500/);
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    fireEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalled();
  });
});
