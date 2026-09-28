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
    // Org-wide total, as returned by the real API on every node (see #1738).
    // The Runners cell must never fall back to this value.
    health: { runners_registered: 21 },
    offline_reason: "runner_service_offline",
    offline_detail: "No online runners or dashboard telemetry are visible.",
    system: {},
  },
];

const mockRunners = [
  { id: 1, name: "d-sorg-local-ControlTower-1", status: "online", busy: false, labels: ["self-hosted"], machine: "ControlTower" },
  { id: 2, name: "d-sorg-local-ControlTower-2", status: "online", busy: true, labels: ["self-hosted"], machine: "ControlTower" },
  { id: 3, name: "d-sorg-local-DeskComputer-1", status: "offline", busy: false, labels: [], machine: "DeskComputer" },
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

  it("triggers onMaintenanceAction when row action menu item is selected", () => {
    const onMaintenanceAction = vi.fn();
    render(
      <FleetMachinesSection
        machines={mockMachines}
        runners={mockRunners}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onMaintenanceAction={onMaintenanceAction}
      />
    );

    const actionTriggers = screen.getAllByRole("button", { name: /Actions/i });
    expect(actionTriggers.length).toBeGreaterThan(0);
    fireEvent.click(actionTriggers[0]);

    const compactDiskItem = screen.getByRole("menuitem", { name: "Compact disk" });
    fireEvent.click(compactDiskItem);

    expect(onMaintenanceAction).toHaveBeenCalledWith("ControlTower", "compact_disk", true);
  });

  it("groups runners by machine and shows the machine's own count, not the org-wide total", () => {
    render(
      <FleetMachinesSection
        machines={mockMachines}
        runners={mockRunners}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />
    );

    const controlTowerRow = screen.getByText("ControlTower").closest("tr");
    const deskComputerRow = screen.getByText("DeskComputer").closest("tr");
    expect(controlTowerRow).not.toBeNull();
    expect(deskComputerRow).not.toBeNull();

    // ControlTower: 2 runners bound, both online -> "2 / 2"
    expect(controlTowerRow!.querySelectorAll("td")[3].textContent).toBe("2 / 2");
    // DeskComputer: 1 runner bound, 0 online -> "0 / 1" (never the org-wide 21)
    expect(deskComputerRow!.querySelectorAll("td")[3].textContent).toBe("0 / 1");
  });
});

