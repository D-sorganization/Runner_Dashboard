// @vitest-environment jsdom
/**
 * Tests for Machines.tsx — decomposition #836 pass 10.
 *
 * Covers the extracted "Machines" tab:
 * 1. Headline stats (machine/runner/GPU counts) aggregate correctly.
 * 2. One MachineCard renders per physical machine, folding runner-pool entries.
 * 3. SystemResourcesPanel renders CPU/RAM/storage and the per-runner table,
 *    sortable, and shows the "metrics unavailable" empty state.
 * 4. StorageDeviceMetric renders a labelled progress row.
 * 5. Loading + offline-stub-node states.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  MachinesTab,
  MachineCard,
  StorageDeviceMetric,
  SystemResourcesPanel,
} from "../Machines";

afterEach(cleanup);

const SYS = {
  cpu: { percent: 42, per_cpu_percent: [10, 90] },
  memory: { percent: 50, used_gb: 8, total_gb: 16, available_gb: 8 },
  disk: { storage_devices: [{ label: "C:", path: "C:", used_gb: 100, total_gb: 200, percent: 50 }] },
  network: { bytes_sent: 2048, bytes_recv: 4096 },
  uptime_seconds: 7200,
  runner_processes: [
    { runner_num: 2, status: "running", cpu_percent: 30, memory_mb: 500, process_count: 3 },
    { runner_num: 1, status: "idle", cpu_percent: 5, memory_mb: 100, process_count: 1 },
  ],
};

describe("MachinesTab", () => {
  it("renders headline stats and a card per physical machine", () => {
    const data = {
      nodes: [
        {
          name: "ControlTower",
          online: true,
          dashboard_reachable: true,
          system: { ...SYS, gpu: { count: 1, gpus: [{ name: "RTX", vram_used_mb: 1, vram_total_mb: 2, vram_percent: 50, gpu_util_percent: 10, temp_c: 40 }] } },
        },
      ],
    };
    const runners = [
      { id: 1, name: "d-sorg-local-ControlTower-1", status: "online", busy: true },
      { id: 2, name: "d-sorg-local-DeskComputer-1", status: "online", busy: false },
    ];
    render(<MachinesTab data={data} runners={runners} loading={false} />);
    expect(screen.getByText("Machines")).toBeInTheDocument();
    expect(screen.getByText("Total Runners")).toBeInTheDocument();
    expect(screen.getByText("GPU Nodes")).toBeInTheDocument();
    // ControlTower (telemetry node) + DeskComputer (runner-only stub) = 2 cards
    expect(screen.getAllByText(/ControlTower|DeskComputer/).length).toBeGreaterThanOrEqual(2);
  });

  it("shows the loading state with no nodes", () => {
    render(<MachinesTab data={{ nodes: [] }} runners={[]} loading={true} />);
    expect(screen.getByText("Loading fleet...")).toBeInTheDocument();
  });

  it("renders an offline stub card for runner-only machines", () => {
    const runners = [
      { id: 9, name: "d-sorg-local-DeskComputer-1", status: "offline", busy: false },
    ];
    render(<MachinesTab data={{ nodes: [] }} runners={runners} loading={false} />);
    expect(screen.getByText("DeskComputer")).toBeInTheDocument();
    expect(screen.getByText(/Runner services offline/)).toBeInTheDocument();
  });
});

describe("MachineCard", () => {
  it("renders runner badges, uptime, and the resources panel", () => {
    const node = {
      name: "ControlTower",
      online: true,
      dashboard_reachable: true,
      role: "hub",
      system: SYS,
    };
    render(
      <MachineCard
        node={node}
        relatedNodes={[node]}
        machineRunners={[
          { id: 1, name: "d-sorg-local-ControlTower-1", status: "online", busy: false },
        ]}
      />,
    );
    expect(screen.getByText("ControlTower")).toBeInTheDocument();
    expect(screen.getByText(/Runners \(1 online, 0 busy\)/)).toBeInTheDocument();
    expect(screen.getByText(/Uptime:/)).toBeInTheDocument();
    expect(screen.getByText("CPU per-core")).toBeInTheDocument();
  });

  it("shows an error/offline reason for an unreachable node", () => {
    const node = {
      name: "OGLaptop",
      online: false,
      dashboard_reachable: false,
      offline_reason: "computer_offline",
      offline_detail: "no ping",
      system: {},
    };
    render(<MachineCard node={node} machineRunners={[]} />);
    expect(screen.getByText(/Computer unreachable/)).toBeInTheDocument();
    expect(screen.getByText(/no ping/)).toBeInTheDocument();
  });
});

describe("SystemResourcesPanel", () => {
  it("renders metrics and a sortable per-runner table", () => {
    render(<SystemResourcesPanel system={SYS} node={{}} relatedNodes={[]} />);
    expect(screen.getByText("CPU per-core")).toBeInTheDocument();
    expect(screen.getByText("RAM")).toBeInTheDocument();
    expect(screen.getByText("Network I/O")).toBeInTheDocument();
    expect(screen.getByText("Per-Runner Resources")).toBeInTheDocument();

    // Default sort is by runner number ascending.
    const rowsBefore = screen.getAllByText(/^runner-\d$/).map((n) => n.textContent);
    expect(rowsBefore).toEqual(["runner-1", "runner-2"]);
    // Clicking the Runner header toggles to descending.
    fireEvent.click(screen.getByText("Runner"));
    const rowsAfter = screen.getAllByText(/^runner-\d$/).map((n) => n.textContent);
    expect(rowsAfter).toEqual(["runner-2", "runner-1"]);
  });

  it("renders the empty state when no cpu/memory metrics", () => {
    render(<SystemResourcesPanel system={{}} relatedNodes={[]} />);
    expect(screen.getByText(/System metrics unavailable/)).toBeInTheDocument();
  });
});

describe("StorageDeviceMetric", () => {
  it("renders a labelled usage row", () => {
    render(
      <table>
        <tbody>
          <tr>
            <td>
              <StorageDeviceMetric
                device={{ label: "D:", path: "D:", used_gb: 50, total_gb: 100, percent: 50 }}
              />
            </td>
          </tr>
        </tbody>
      </table>,
    );
    expect(screen.getByText("D:")).toBeInTheDocument();
    expect(screen.getByText(/50 \/ 100 GB \(50%\)/)).toBeInTheDocument();
  });
});
