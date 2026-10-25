/**
 * fleetMachines.ts — pure, React-free fleet/machine helpers shared by tabs
 * extracted from the legacy `App.tsx` monolith (decomposition #836, pass 10).
 *
 * Reproduces, 1:1, the legacy machine-identity, node-quality, telemetry-
 * visibility and storage-device helpers. Kept here (a `.ts` file with no JSX)
 * so both the legacy `App.tsx` (Fleet/Overview tab) and the extracted
 * `pages/Machines.tsx` consume a single definition — no fork, no double-poll.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 1:1 port of dynamically-typed legacy fleet/telemetry node payloads; the backend response shapes lack complete TypeScript definitions. */

/** Canonicalises a raw machine/runner name to its physical-machine display name. */
export function canonicalMachineName(name: unknown): string {
  const raw = String(name || "").trim();
  const key = raw.toLowerCase().replace(/[^a-z0-9]/g, "");
  const aliases: Record<string, string> = {
    desktop: "DeskComputer",
    deskcomputer: "DeskComputer",
    desk: "DeskComputer",
    oglaptop: "OGLaptop",
    og: "OGLaptop",
    controltower: "ControlTower",
    controltowernvme: "ControlTower",
    controltowerssd: "ControlTower",
    controltowermatlab: "ControlTower",
    controltowermatlabssd: "ControlTower",
    controltowerrunnermonitoring: "ControlTower",
  };
  return aliases[key] || raw || "Unknown";
}

export interface ParsedRunnerName {
  machine: string;
  number: number;
}

/** Parses a runner name into `{ machine, number }`, mirroring legacy logic. */
export function parseRunnerName(name: unknown): ParsedRunnerName {
  const s = String(name || "");
  const match = s.match(/^d-sorg-local-(.+)-(\d+)$/);
  if (match) {
    return { machine: canonicalMachineName(match[1]), number: Number(match[2]) };
  }
  const matlabMatch = s.match(/^(.+)-MATLAB$/i);
  if (matlabMatch) {
    return { machine: canonicalMachineName(matlabMatch[1]), number: 9998 };
  }
  return { machine: "Unknown", number: 999999 };
}

/** Sort comparator for runners: ControlTower first, then by machine + number. */
export function runnerSort(
  a: { name?: unknown },
  b: { name?: unknown },
): number {
  const pa = parseRunnerName(a.name);
  const pb = parseRunnerName(b.name);
  if (pa.machine !== pb.machine) {
    if (pa.machine === "ControlTower") return -1;
    if (pb.machine === "ControlTower") return 1;
    return pa.machine.localeCompare(pb.machine);
  }
  return pa.number - pb.number;
}

/** True when a node exposes any live CPU/memory/disk system metric. */
export function nodeHasSystemMetrics(n: any): boolean {
  const sys = (n && n.system) || {};
  return !!(
    (sys.cpu && sys.cpu.percent != null) ||
    (sys.memory && sys.memory.percent != null) ||
    (sys.disk && sys.disk.percent != null)
  );
}

/** Heuristic score used to pick the "best" telemetry node for a machine. */
export function nodeQualityScore(n: any): number {
  let score = 0;
  if (!n) return score;
  if (n.is_local) score += 100;
  if (n.dashboard_reachable !== false) score += 40;
  if (n.online) score += 20;
  if (nodeHasSystemMetrics(n)) score += 60;
  if (n.role === "hub") score += 5;
  if (n.role === "runner_pool") score -= 10;
  return score;
}

/** Human-readable label for a node's `offline_reason` code. */
export function offlineReasonLabel(reason: string | null | undefined): string {
  return {
    wsl_connection_lost: "WSL/dashboard connection lost",
    resource_monitoring: "Taken offline by resource monitoring",
    computer_offline: "Computer unreachable",
    dashboard_unhealthy: "Dashboard unhealthy",
    dashboard_not_deployed: "Dashboard not deployed",
    runner_service_offline: "Runner services offline",
    unknown: "Unknown",
  }[reason || "unknown"] as string;
}

export interface VisibilitySnapshot {
  state: string;
  label: string;
  detail: string;
}

/** Derives the telemetry-visibility state purely from node + online count. */
export function visibilitySnapshot(
  node: any,
  onlineCount: number,
): VisibilitySnapshot {
  const system = node.system || {};
  const hasSystemMetrics = Object.keys(system).length > 0;
  const hasRunnerTelemetry = !!node.online || onlineCount > 0;
  if (node.offline_reason === "resource_monitoring") {
    return {
      state: "degraded",
      label: "Degraded",
      detail:
        node.offline_detail ||
        "Resource pressure is high enough to warrant attention.",
    };
  }
  if (
    hasRunnerTelemetry &&
    node.dashboard_reachable !== false &&
    hasSystemMetrics
  ) {
    return {
      state: "full_telemetry",
      label: "Full telemetry",
      detail: "Runner status and system metrics are both available.",
    };
  }
  if (hasRunnerTelemetry) {
    return {
      state: "runners_only",
      label: "Runners only",
      detail:
        onlineCount > 0
          ? "Runner registrations are healthy, but dashboard telemetry is unavailable."
          : "Runner telemetry is available, but dashboard visibility is partial.",
    };
  }
  if (node.dashboard_reachable !== false) {
    return {
      state: "dashboard_only",
      label: "Dashboard only",
      detail: "Dashboard is reachable, but runner registrations are offline.",
    };
  }
  return {
    state: "offline",
    label: "Offline",
    detail:
      node.offline_detail ||
      node.error ||
      "No live telemetry from this machine.",
  };
}

/** Resolves the effective visibility, honouring a backend-provided override. */
export function resolveVisibility(
  node: any,
  onlineCount: number,
): VisibilitySnapshot {
  const computed = visibilitySnapshot(node, onlineCount);
  if (!node.visibility_state) return computed;
  if (
    onlineCount > 0 &&
    (node.visibility_state === "dashboard_only" ||
      node.visibility_state === "offline")
  ) {
    return computed;
  }
  return {
    state: node.visibility_state,
    label: node.visibility_label || node.visibility_state,
    detail:
      node.visibility_detail ||
      "Runner status and system metrics are available.",
  };
}

export interface StorageDevice {
  label?: string;
  kind?: string;
  path?: string;
  percent?: number;
  used_gb?: number;
  total_gb?: number;
  [key: string]: unknown;
}

/** Collects de-duplicated storage devices from a system + its related nodes. */
export function collectStorageDevices(
  system: any,
  relatedNodes: any[] | undefined,
): StorageDevice[] {
  const devices: StorageDevice[] = [];
  const seen: Record<string, boolean> = {};
  function addDevice(device: any, fallbackLabel: string | undefined): void {
    if (!device) return;
    const label = device.label || fallbackLabel || device.path || "Storage";
    const key = [label, device.kind || "", device.path || ""].join("|");
    if (seen[key]) return;
    seen[key] = true;
    devices.push(Object.assign({}, device, { label }));
  }
  function addFromSystem(sys: any, prefix: string): void {
    const disk = (sys && sys.disk) || {};
    (disk.storage_devices || []).forEach(function (device: any) {
      addDevice(device, device.label || prefix);
    });
    if (
      (!disk.storage_devices || disk.storage_devices.length === 0) &&
      disk.windows_host
    ) {
      addDevice(disk.windows_host, prefix ? prefix + " Disk" : "Host Disk");
    }
    if (
      (!disk.storage_devices || disk.storage_devices.length === 0) &&
      disk.percent != null
    ) {
      addDevice(disk, prefix ? prefix + " WSL" : "WSL Disk");
    }
  }
  addFromSystem(system, "");
  (relatedNodes || []).forEach(function (node: any) {
    addFromSystem(node.system, node.name);
  });
  return devices;
}
