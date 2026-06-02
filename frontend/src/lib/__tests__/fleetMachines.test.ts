/**
 * Tests for lib/fleetMachines.ts — decomposition #836 pass 10.
 *
 * Covers the pure fleet/machine helpers extracted from legacy App.tsx:
 * name canonicalisation, runner-name parsing + sort, node-quality scoring,
 * offline-reason labels, telemetry-visibility resolution, and storage-device
 * collection/de-duplication.
 */
import { describe, expect, it } from "vitest";
import {
  canonicalMachineName,
  collectStorageDevices,
  nodeHasSystemMetrics,
  nodeQualityScore,
  offlineReasonLabel,
  parseRunnerName,
  resolveVisibility,
  runnerSort,
  visibilitySnapshot,
} from "../fleetMachines";

describe("canonicalMachineName", () => {
  it("maps known aliases to physical machine names", () => {
    expect(canonicalMachineName("desktop")).toBe("DeskComputer");
    expect(canonicalMachineName("ControlTower-NVMe")).toBe("ControlTower");
    expect(canonicalMachineName("controltower-ssd")).toBe("ControlTower");
    expect(canonicalMachineName("OG")).toBe("OGLaptop");
  });
  it("passes through unknown names and defaults empties to Unknown", () => {
    expect(canonicalMachineName("Random-Box")).toBe("Random-Box");
    expect(canonicalMachineName("")).toBe("Unknown");
    expect(canonicalMachineName(null)).toBe("Unknown");
  });
});

describe("parseRunnerName", () => {
  it("parses d-sorg-local runner names", () => {
    expect(parseRunnerName("d-sorg-local-ControlTower-3")).toEqual({
      machine: "ControlTower",
      number: 3,
    });
  });
  it("parses MATLAB runner names with sentinel number", () => {
    expect(parseRunnerName("DeskComputer-MATLAB")).toEqual({
      machine: "DeskComputer",
      number: 9998,
    });
  });
  it("falls back to Unknown for unrecognised names", () => {
    expect(parseRunnerName("weird")).toEqual({
      machine: "Unknown",
      number: 999999,
    });
  });
});

describe("runnerSort", () => {
  it("sorts ControlTower first, then machine, then number", () => {
    const runners = [
      { name: "d-sorg-local-DeskComputer-2" },
      { name: "d-sorg-local-ControlTower-5" },
      { name: "d-sorg-local-ControlTower-1" },
      { name: "d-sorg-local-DeskComputer-1" },
    ];
    const sorted = runners.slice().sort(runnerSort).map((r) => r.name);
    expect(sorted).toEqual([
      "d-sorg-local-ControlTower-1",
      "d-sorg-local-ControlTower-5",
      "d-sorg-local-DeskComputer-1",
      "d-sorg-local-DeskComputer-2",
    ]);
  });
  it("keeps ControlTower ahead when it is the second operand", () => {
    expect(
      runnerSort(
        { name: "d-sorg-local-DeskComputer-1" },
        { name: "d-sorg-local-ControlTower-1" },
      ),
    ).toBe(1);
  });
});

describe("nodeHasSystemMetrics", () => {
  it("detects any cpu/memory/disk percent", () => {
    expect(nodeHasSystemMetrics({ system: { cpu: { percent: 12 } } })).toBe(true);
    expect(nodeHasSystemMetrics({ system: { memory: { percent: 0 } } })).toBe(true);
    expect(nodeHasSystemMetrics({ system: { disk: { percent: 50 } } })).toBe(true);
  });
  it("returns false with no metrics or no node", () => {
    expect(nodeHasSystemMetrics({ system: {} })).toBe(false);
    expect(nodeHasSystemMetrics(null)).toBe(false);
  });
});

describe("nodeQualityScore", () => {
  it("scores a fully-local metric-rich node highest", () => {
    const score = nodeQualityScore({
      is_local: true,
      dashboard_reachable: true,
      online: true,
      system: { cpu: { percent: 5 } },
      role: "hub",
    });
    expect(score).toBe(100 + 40 + 20 + 60 + 5);
  });
  it("penalises runner_pool role and returns 0 for null", () => {
    expect(nodeQualityScore({ role: "runner_pool", dashboard_reachable: false })).toBe(-10);
    expect(nodeQualityScore(null)).toBe(0);
  });
});

describe("offlineReasonLabel", () => {
  it("maps known codes and defaults unknown", () => {
    expect(offlineReasonLabel("dashboard_not_deployed")).toBe("Dashboard not deployed");
    expect(offlineReasonLabel("resource_monitoring")).toBe(
      "Taken offline by resource monitoring",
    );
    expect(offlineReasonLabel(null)).toBe("Unknown");
    expect(offlineReasonLabel("nonsense")).toBeUndefined();
  });
});

describe("visibilitySnapshot", () => {
  it("flags resource_monitoring as degraded", () => {
    expect(visibilitySnapshot({ offline_reason: "resource_monitoring" }, 0).state).toBe(
      "degraded",
    );
  });
  it("reports full telemetry when runners + dashboard + metrics present", () => {
    const snap = visibilitySnapshot(
      { online: true, dashboard_reachable: true, system: { cpu: { percent: 1 } } },
      2,
    );
    expect(snap.state).toBe("full_telemetry");
  });
  it("reports runners_only, dashboard_only and offline branches", () => {
    expect(visibilitySnapshot({ online: true, system: {} }, 0).state).toBe("runners_only");
    expect(
      visibilitySnapshot({ online: false, dashboard_reachable: true, system: {} }, 0).state,
    ).toBe("dashboard_only");
    expect(
      visibilitySnapshot({ online: false, dashboard_reachable: false, system: {} }, 0).state,
    ).toBe("offline");
  });
});

describe("resolveVisibility", () => {
  it("returns computed snapshot when no backend override", () => {
    expect(resolveVisibility({ online: true, system: {} }, 1).state).toBe("runners_only");
  });
  it("honours a backend override label", () => {
    const r = resolveVisibility(
      { visibility_state: "full_telemetry", visibility_label: "All good" },
      0,
    );
    expect(r.state).toBe("full_telemetry");
    expect(r.label).toBe("All good");
  });
  it("ignores stale dashboard_only override when runners are online", () => {
    const r = resolveVisibility(
      { visibility_state: "dashboard_only", online: true, system: {} },
      3,
    );
    expect(r.state).toBe("runners_only");
  });
});

describe("collectStorageDevices", () => {
  it("collects and de-duplicates explicit storage devices", () => {
    const sys = {
      disk: {
        storage_devices: [
          { label: "C:", kind: "ssd", path: "C:", percent: 50 },
          { label: "C:", kind: "ssd", path: "C:", percent: 50 },
        ],
      },
    };
    expect(collectStorageDevices(sys, [])).toHaveLength(1);
  });
  it("falls back to windows_host then WSL disk", () => {
    const host = collectStorageDevices({ disk: { windows_host: { percent: 10 } } }, []);
    expect(host[0].label).toBe("Host Disk");
    const wsl = collectStorageDevices({ disk: { percent: 20 } }, []);
    expect(wsl[0].label).toBe("WSL Disk");
  });
  it("includes prefixed devices from related nodes", () => {
    const devices = collectStorageDevices(
      {},
      [{ name: "DeskComputer", system: { disk: { percent: 30 } } }],
    );
    expect(devices[0].label).toBe("DeskComputer WSL");
  });
});
