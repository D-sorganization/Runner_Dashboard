/**
 * Machines.tsx — the "Machines" tab, extracted (behaviour 1:1) from the legacy
 * `App.tsx` monolith as part of the decomposition epic (#836, pass 10).
 *
 * Renders a per-physical-machine grid: each `MachineCard` folds together its
 * runner-pool entries (e.g. ControlTower-NVMe + ControlTower-SSD) and the best
 * live telemetry node, then embeds the full `SystemResourcesPanel`
 * (CPU/RAM/swap/storage/load/network/GPU + per-runner process table).
 *
 * `MachinesPage` owns the fleet-node + runner fetches for the routed desktop
 * shell. `MachinesTab` stays presentational so legacy callers and focused
 * tests can keep passing explicit payloads.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 1:1 port of dynamically-typed legacy fleet/telemetry payloads; the backend response shapes lack complete TypeScript definitions. */
import React, { useCallback, useEffect, useState } from "react";
import { Stat } from "../components/Stat";
import {
  boundedPercent,
  cpuColor,
  pColor,
  formatBytes,
  timeAgo,
} from "../components/formatters";
import { sortRows, type SortState } from "./decompSort";
import { SortTh } from "./decompSortTh";
import {
  canonicalMachineName,
  collectStorageDevices,
  nodeQualityScore,
  offlineReasonLabel,
  parseRunnerName,
  resolveVisibility,
  runnerSort,
  type StorageDevice,
} from "../lib/fleetMachines";
import { legacyFetch } from "../lib/api";

const h = React.createElement;

interface RunnersPayload {
  runners?: any[];
}

function normalizeRunnersPayload(payload: unknown): any[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object") {
    const runners = (payload as RunnersPayload).runners;
    if (Array.isArray(runners)) return runners;
  }
  return [];
}

export function MachinesPage(): React.ReactElement {
  const [data, setData] = useState<Record<string, any>>({ nodes: [] });
  const [runners, setRunners] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    Promise.all([
      legacyFetch("/api/fleet/nodes", { signal }).then((r) => {
        if (!r.ok) throw new Error("fleet nodes HTTP " + r.status);
        return r.json();
      }),
      legacyFetch("/api/runners", { signal }).then((r) => {
        if (!r.ok) throw new Error("runners HTTP " + r.status);
        return r.json();
      }),
    ])
      .then(([nodesPayload, runnersPayload]) => {
        setData(
          nodesPayload && typeof nodesPayload === "object"
            ? (nodesPayload as Record<string, any>)
            : { nodes: [] },
        );
        setRunners(normalizeRunnersPayload(runnersPayload));
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err.message : "Failed to load machine data",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <div>
      {error ? (
        <div
          className="section"
          role="alert"
          style={{ marginBottom: 12, color: "var(--accent-red)" }}
        >
          Failed to load machine data: {error}
          <button
            className="btn"
            type="button"
            onClick={() => refresh()}
            style={{ marginLeft: 12 }}
          >
            Retry
          </button>
        </div>
      ) : null}
      <MachinesTab data={data} runners={runners} loading={loading} />
    </div>
  );
}

export function StorageDeviceMetric(p: { device?: StorageDevice }): React.ReactElement {
  const device = p.device || {};
  const pct = boundedPercent(device.percent || 0);
  return h(
    "div",
    { style: { marginBottom: 8 } },
    h(
      "div",
      { className: "metric-row" },
      h("span", { className: "metric-label" }, device.label || "Storage"),
      h(
        "span",
        { className: "metric-value" },
        device.used_gb +
          " / " +
          device.total_gb +
          " GB (" +
          device.percent +
          "%)",
      ),
    ),
    h(
      "div",
      { className: "progress-bar" },
      h("div", {
        className: "progress-fill " + pColor(pct),
        style: { width: pct + "%" },
        title: device.path || "",
      }),
    ),
  );
}

export function SystemResourcesPanel(p: any): React.ReactElement {
  const sys = p.system || {};
  const relatedNodes = p.relatedNodes || [];
  const cpu = sys.cpu || {};
  const mem = sys.memory || {};
  const disk = sys.disk || {};
  const diskPressure = disk.pressure || {};
  const storageDevices = collectStorageDevices(sys, relatedNodes);
  const net = sys.network || {};
  const gpus = (sys.gpu && sys.gpu.gpus) || [];
  const rprocs = sys.runner_processes || [];
  const procSortState = React.useState<SortState>({ key: "runner", dir: "asc" });
  const procSort = procSortState[0],
    setProcSort = procSortState[1];
  const procAccessors = {
    runner: function (rp: any) {
      return rp.runner_num || 0;
    },
    status: function (rp: any) {
      return rp.status || "";
    },
    cpu: function (rp: any) {
      return rp.cpu_percent || 0;
    },
    memory: function (rp: any) {
      return rp.memory_mb || 0;
    },
    procs: function (rp: any) {
      return rp.process_count || 0;
    },
  };
  const sortedRprocs = sortRows(rprocs, procSort, procAccessors);
  if (!cpu.percent && !mem.percent)
    return h(
      "div",
      {
        style: {
          color: "var(--text-muted)",
          padding: 20,
          textAlign: "center",
          fontSize: 13,
        },
      },
      "System metrics unavailable — dashboard port forwarding needed on this machine",
    );
  return h(
    "div",
    null,
    h(
      "div",
      { style: { marginBottom: 16 } },
      h(
        "div",
        { className: "metric-row" },
        h("span", { className: "metric-label" }, "CPU per-core"),
        h(
          "span",
          { className: "metric-value" },
          cpu.percent != null ? cpu.percent + "% avg" : "-",
        ),
      ),
      cpu.per_cpu_percent
        ? h(
            "div",
            { className: "cpu-heatmap" },
            cpu.per_cpu_percent.map(function (v: number, i: number) {
              return h(
                "div",
                {
                  className: "cpu-core",
                  key: i,
                  style: {
                    background: cpuColor(v),
                    color:
                      v > 50
                        ? "var(--text-on-accent)"
                        : "var(--text-secondary)",
                  },
                  title: "Core " + i + ": " + v + "%",
                },
                Math.round(v),
              );
            }),
          )
        : null,
    ),
    mem.percent != null
      ? h(
          "div",
          { style: { marginBottom: 12 } },
          h(
            "div",
            { className: "metric-row" },
            h("span", { className: "metric-label" }, "RAM"),
            h(
              "span",
              { className: "metric-value" },
              (function () {
                const usedPct = mem.total_gb
                  ? Math.round((1 - mem.available_gb / mem.total_gb) * 100)
                  : Math.round(mem.percent || 0);
                const label = mem.source === "wsl" ? "WSL" : "Host";
                return (
                  label +
                  " " +
                  mem.used_gb +
                  " / " +
                  mem.total_gb +
                  " GB (" +
                  usedPct +
                  "%)"
                );
              })(),
            ),
          ),
          h(
            "div",
            { className: "progress-bar" },
            h("div", {
              className:
                "progress-fill " +
                pColor(
                  mem.total_gb
                    ? Math.round((1 - mem.available_gb / mem.total_gb) * 100)
                    : Math.round(mem.percent || 0),
                ),
              style: {
                width:
                  (mem.total_gb
                    ? Math.round((1 - mem.available_gb / mem.total_gb) * 100)
                    : Math.round(mem.percent || 0)) + "%",
              },
            }),
          ),
          mem.host && mem.wsl
            ? h(
                "div",
                { className: "stat-sub", style: { marginTop: 4 } },
                "WSL " +
                  mem.wsl.used_gb +
                  " / " +
                  mem.wsl.total_gb +
                  " GB" +
                  (mem.host.stale ? " · host probe stale" : ""),
              )
            : null,
        )
      : null,
    mem.swap_total_gb > 0
      ? h(
          "div",
          { style: { marginBottom: 12 } },
          h(
            "div",
            { className: "metric-row" },
            h("span", { className: "metric-label" }, "Swap"),
            h(
              "span",
              { className: "metric-value" },
              mem.swap_used_gb + " / " + mem.swap_total_gb + " GB",
            ),
          ),
          h(
            "div",
            { className: "progress-bar" },
            h("div", {
              className: "progress-fill purple",
              style: { width: mem.swap_percent + "%" },
            }),
          ),
        )
      : null,
    storageDevices.length > 0
      ? h(
          "div",
          { style: { marginBottom: 12 } },
          storageDevices.map(function (device) {
            return h(StorageDeviceMetric, {
              key: (device.label || "") + "|" + (device.path || ""),
              device: device,
            });
          }),
          diskPressure.status && diskPressure.status !== "healthy"
            ? h(
                "div",
                {
                  className:
                    diskPressure.status === "critical"
                      ? "storage-critical"
                      : "storage-warning",
                  style: { fontSize: 12, marginTop: 6 },
                },
                "Storage " +
                  diskPressure.status +
                  ": " +
                  (diskPressure.reasons || []).join(", "),
              )
            : null,
        )
      : null,
    cpu.load_avg_1m != null
      ? h(
          "div",
          { className: "metric-row", style: { marginBottom: 12 } },
          h("span", { className: "metric-label" }, "Load Average"),
          h(
            "span",
            { className: "metric-value" },
            cpu.load_avg_1m + " / " + cpu.load_avg_5m + " / " + cpu.load_avg_15m,
          ),
        )
      : null,
    net.bytes_sent != null
      ? h(
          "div",
          { className: "metric-row", style: { marginBottom: 12 } },
          h("span", { className: "metric-label" }, "Network I/O"),
          h(
            "span",
            { className: "metric-value" },
            "↑ " +
              formatBytes(net.bytes_sent) +
              "  ↓ " +
              formatBytes(net.bytes_recv),
          ),
        )
      : null,
    gpus.length > 0
      ? gpus.map(function (g: any, i: number) {
          return h(
            "div",
            { className: "gpu-card", key: i },
            h("div", { className: "gpu-name" }, "🎮 ", g.name),
            h(
              "div",
              { className: "metric-row" },
              h("span", { className: "metric-label" }, "VRAM"),
              h(
                "span",
                { className: "metric-value" },
                g.vram_used_mb +
                  " / " +
                  g.vram_total_mb +
                  " MB (" +
                  g.vram_percent +
                  "%)",
              ),
            ),
            h(
              "div",
              { className: "progress-bar", style: { marginBottom: 8 } },
              h("div", {
                className: "progress-fill purple",
                style: { width: g.vram_percent + "%" },
              }),
            ),
            h(
              "div",
              { className: "metric-row" },
              h("span", { className: "metric-label" }, "GPU Util"),
              h("span", { className: "metric-value" }, g.gpu_util_percent + "%"),
            ),
            h(
              "div",
              { className: "metric-row" },
              h("span", { className: "metric-label" }, "Temp"),
              h(
                "span",
                {
                  className: "metric-value",
                  style: {
                    color: g.temp_c > 80 ? "var(--accent-red)" : "inherit",
                  },
                },
                g.temp_c + "°C",
              ),
            ),
            g.power_draw_w != null
              ? h(
                  "div",
                  { className: "metric-row" },
                  h("span", { className: "metric-label" }, "Power"),
                  h(
                    "span",
                    { className: "metric-value" },
                    g.power_draw_w + "W / " + g.power_limit_w + "W",
                  ),
                )
              : null,
          );
        })
      : null,
    rprocs.length > 0
      ? h(
          "div",
          { style: { marginTop: 12 } },
          h(
            "div",
            {
              className: "metric-label",
              style: { marginBottom: 8, fontSize: 13, fontWeight: 600 },
            },
            "Per-Runner Resources",
          ),
          h(
            "table",
            { className: "resource-table" },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h(SortTh, {
                  label: "Runner",
                  sortKey: "runner",
                  sort: procSort,
                  setSort: setProcSort,
                }),
                h(SortTh, {
                  label: "Status",
                  sortKey: "status",
                  sort: procSort,
                  setSort: setProcSort,
                }),
                h(SortTh, {
                  label: "CPU %",
                  sortKey: "cpu",
                  sort: procSort,
                  setSort: setProcSort,
                }),
                h(SortTh, {
                  label: "Memory",
                  sortKey: "memory",
                  sort: procSort,
                  setSort: setProcSort,
                }),
                h(SortTh, {
                  label: "Procs",
                  sortKey: "procs",
                  sort: procSort,
                  setSort: setProcSort,
                }),
              ),
            ),
            h(
              "tbody",
              null,
              sortedRprocs.map(function (rp: any) {
                return h(
                  "tr",
                  { key: rp.runner_num },
                  h("td", null, "runner-" + rp.runner_num),
                  h(
                    "td",
                    null,
                    h(
                      "span",
                      {
                        className:
                          "runner-status-badge " +
                          (rp.status === "running" ? "online" : "offline"),
                      },
                      rp.status,
                    ),
                  ),
                  h("td", null, rp.cpu_percent + "%"),
                  h("td", null, rp.memory_mb + " MB"),
                  h("td", null, rp.process_count),
                );
              }),
            ),
          ),
        )
      : null,
  );
}

export function MachineCard(p: any): React.ReactElement {
  const n = p.node;
  const relatedNodes = p.relatedNodes || n.related_nodes || [n];
  const machineRunners = p.machineRunners || [];
  const sys = n.system || {};
  const busyCount = machineRunners.filter(function (r: any) {
    return r.busy;
  }).length;
  const onlineCount = machineRunners.filter(function (r: any) {
    return r.status === "online";
  }).length;
  const visibility = resolveVisibility(n, onlineCount);
  // Machine is "live" if it has any online runners OR its dashboard is reachable.
  const isLive = !!n.online || onlineCount > 0;
  const dashboardReachable =
    n.dashboard_reachable !== false && !!sys.uptime_seconds;
  const uptimeStr = (function () {
    const s = sys.uptime_seconds;
    if (!s) return dashboardReachable ? "-" : "dashboard not deployed";
    const hr = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (hr > 24) return Math.floor(hr / 24) + "d " + (hr % 24) + "h";
    return hr + "h " + m + "m";
  })();
  const mColors: Record<string, string> = {
    ControlTower: "var(--accent-purple)",
    DeskComputer: "var(--accent-blue)",
    OGLaptop: "var(--accent-orange)",
  };
  const mColor = mColors[n.name] || "var(--accent-blue)";
  const dotClass = isLive ? (dashboardReachable ? "green" : "yellow") : "red";
  const offlineReason =
    n.offline_reason ||
    (!dashboardReachable && isLive
      ? "dashboard_not_deployed"
      : !isLive
        ? "unknown"
        : null);

  return h(
    "div",
    {
      className: "machine-card" + (isLive ? "" : " offline"),
      style: { borderLeft: "3px solid " + mColor },
    },
    h(
      "div",
      { className: "machine-card-header" },
      h(
        "div",
        { className: "machine-name" },
        h("span", { className: "status-dot " + dotClass }),
        n.name,
      ),
      h(
        "div",
        { className: "machine-badges" },
        h(
          "span",
          { className: "role-badge " + (n.role || "node") },
          n.role || "node",
        ),
        h(
          "span",
          {
            className: "telemetry-badge " + (visibility.state || "offline"),
            title: visibility.detail,
          },
          visibility.label,
        ),
        n.telemetry_schema && n.telemetry_schema !== "current"
          ? h(
              "span",
              {
                className: "role-badge",
                title:
                  n.telemetry_schema === "legacy"
                    ? "This node is reachable but is serving an older telemetry schema. Deploy the current dashboard to restore full metrics."
                    : "This node is not returning system telemetry.",
                style: {
                  background: "rgba(210,153,34,0.12)",
                  color: "var(--accent-yellow)",
                  border: "1px solid rgba(210,153,34,0.3)",
                },
              },
              n.telemetry_schema === "legacy" ? "legacy metrics" : "no metrics",
            )
          : null,
        n.dashboard_version
          ? h(
              "span",
              {
                className: "role-badge",
                title: "Dashboard version " + n.dashboard_version,
              },
              "v" + n.dashboard_version,
            )
          : null,
        n.is_local
          ? h(
              "span",
              {
                className: "role-badge",
                style: {
                  background: "rgba(63,185,80,0.1)",
                  color: "var(--accent-green)",
                  border: "1px solid rgba(63,185,80,0.3)",
                },
              },
              "this machine",
            )
          : null,
        h(
          "span",
          { style: { color: "var(--text-muted)", fontSize: 12 } },
          "Uptime: " + uptimeStr,
        ),
      ),
    ),

    // Runners summary
    h(
      "div",
      {
        className: "machine-runners",
        style: { flexDirection: "column", alignItems: "stretch", gap: 6 },
      },
      h(
        "div",
        {
          style: {
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          },
        },
        h(
          "span",
          {
            style: {
              color: "var(--text-secondary)",
              fontSize: 13,
              fontWeight: 600,
            },
          },
          "Runners (" + onlineCount + " online, " + busyCount + " busy)",
        ),
      ),
      machineRunners.length > 0
        ? h(
            "div",
            {
              style: {
                display: "flex",
                flexWrap: "wrap",
                gap: 4,
                marginTop: 4,
              },
            },
            machineRunners.map(function (r: any) {
              const color = r.busy
                ? "var(--accent-yellow)"
                : r.status === "online"
                  ? "var(--accent-green)"
                  : "var(--accent-red)";
              const label = r.name.split("-").pop();
              const title =
                r.name +
                (r.busy
                  ? " (busy)"
                  : r.status === "online"
                    ? " (idle)"
                    : " (offline)");
              return h(
                "span",
                {
                  key: r.id,
                  title: title,
                  style: {
                    background: color + "22",
                    color: color,
                    padding: "2px 8px",
                    borderRadius: 10,
                    fontSize: 11,
                    fontWeight: 600,
                    border: "1px solid " + color + "44",
                  },
                },
                label,
              );
            }),
          )
        : null,
    ),

    // Full system resources panel
    h(
      "div",
      {
        style: {
          marginTop: 12,
          borderTop: "1px solid var(--border)",
          paddingTop: 12,
        },
      },
      h(SystemResourcesPanel, {
        system: sys,
        node: n,
        relatedNodes: relatedNodes,
      }),
    ),

    // Error
    offlineReason
      ? h(
          "div",
          { className: "machine-error" },
          offlineReasonLabel(offlineReason),
          n.offline_detail || n.error
            ? h(
                "span",
                { style: { color: "var(--text-muted)" } },
                " — " + (n.offline_detail || n.error),
              )
            : null,
        )
      : n.error
        ? h("div", { className: "machine-error" }, n.error)
        : null,

    // Last seen
    n.last_seen
      ? h(
          "div",
          { className: "machine-last-seen" },
          "Updated: " + timeAgo(n.last_seen),
        )
      : null,
  );
}

export function MachinesTab(p: any): React.ReactElement {
  const d = p.data || {};
  const loading = p.loading;
  const allRunners = p.runners || [];
  const nodes = d.nodes || [];
  // Group runners by machine name
  const runnersByMachine: Record<string, any[]> = {};
  allRunners.forEach(function (r: any) {
    const machine = parseRunnerName(r.name).machine;
    if (!runnersByMachine[machine]) runnersByMachine[machine] = [];
    runnersByMachine[machine].push(r);
  });
  Object.keys(runnersByMachine).forEach(function (name) {
    runnersByMachine[name] = runnersByMachine[name].slice().sort(runnerSort);
  });
  const totalBusy = allRunners.filter(function (r: any) {
    return r.busy;
  }).length;
  const totalOnline = allRunners.filter(function (r: any) {
    return r.status === "online";
  }).length;

  const nodesByPhysicalName: Record<string, any[]> = {};
  nodes.forEach(function (n: any) {
    const physicalName = canonicalMachineName(n.parent_machine || n.name);
    const key = physicalName.toLowerCase();
    if (!nodesByPhysicalName[key]) nodesByPhysicalName[key] = [];
    nodesByPhysicalName[key].push(n);
  });

  // Build machine list from both runners and registry/fleet nodes. Runner pool
  // entries such as ControlTower-NVMe and ControlTower-SSD are folded into the
  // same physical machine card, and the best live telemetry node wins.
  const machineNameSet: Record<string, boolean> = {};
  Object.keys(runnersByMachine).forEach(function (name) {
    machineNameSet[canonicalMachineName(name)] = true;
  });
  Object.keys(nodesByPhysicalName).forEach(function (key) {
    const group = nodesByPhysicalName[key] || [];
    const display = canonicalMachineName(
      (group[0] && (group[0].parent_machine || group[0].name)) || key,
    );
    machineNameSet[display] = true;
  });
  const machineNames = Object.keys(machineNameSet).sort(function (a, b) {
    return a === "ControlTower"
      ? -1
      : b === "ControlTower"
        ? 1
        : a.localeCompare(b);
  });

  const allNodes = machineNames.map(function (name) {
    const related = (nodesByPhysicalName[name.toLowerCase()] || []).slice();
    related.sort(function (a, b) {
      return nodeQualityScore(b) - nodeQualityScore(a);
    });
    const node = related[0];
    if (node) {
      return Object.assign({}, node, {
        name: name,
        related_nodes: related,
        physical_machine: name,
      });
    }
    // Create a stub node from runner data (no backend entry at all)
    const mrs = runnersByMachine[name] || [];
    const mOnline = mrs.filter(function (r: any) {
      return r.status === "online";
    }).length;
    return {
      name: name,
      url: "",
      online: mOnline > 0,
      dashboard_reachable: false,
      is_local: false,
      role: "node",
      system: {},
      health: { runners_registered: mrs.length },
      last_seen: null,
      offline_reason:
        mOnline > 0 ? "dashboard_not_deployed" : "runner_service_offline",
      offline_detail:
        mOnline > 0
          ? "Runner registrations are healthy, but dashboard telemetry is unavailable."
          : "No online runners are registered for this machine.",
      error:
        mOnline > 0
          ? "Dashboard not deployed on this machine — runners are healthy, but per-machine system metrics are unavailable. See docs/dashboard_deployment_guide.md for install steps."
          : "Offline",
      related_nodes: [],
      physical_machine: name,
    };
  });
  const gpuNodes = allNodes.filter(function (n: any) {
    return n.online && n.system && n.system.gpu && n.system.gpu.count > 0;
  });

  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Machines",
        value: allNodes.length,
        sub:
          allNodes.filter(function (n: any) {
            return n.online;
          }).length +
          "/" +
          allNodes.length +
          " online",
        color:
          allNodes.every(function (n: any) {
            return n.online;
          }) && allNodes.length > 0
            ? "var(--accent-green)"
            : "var(--accent-yellow)",
      }),
      h(Stat, {
        label: "Total Runners",
        value: allRunners.length,
        sub: totalOnline + " online, " + totalBusy + " busy",
        color: totalBusy > 0 ? "var(--accent-yellow)" : "var(--accent-green)",
      }),
      h(Stat, {
        label: "GPU Nodes",
        value: gpuNodes.length,
        color: gpuNodes.length > 0 ? "var(--accent-purple)" : "inherit",
        sub:
          gpuNodes
            .map(function (n: any) {
              return n.name;
            })
            .join(", ") || "none detected",
      }),
      h(Stat, {
        label: "Auto-refresh",
        value: "60s",
        sub: "fleet metrics",
      }),
    ),
    loading && allNodes.length === 0
      ? h(
          "div",
          {
            style: {
              textAlign: "center",
              padding: 40,
              color: "var(--text-muted)",
            },
          },
          "Loading fleet...",
        )
      : null,
    allNodes.length > 0
      ? h(
          "div",
          { className: "machine-grid" },
          allNodes.map(function (n: any) {
            return h(MachineCard, {
              key: n.name,
              node: n,
              relatedNodes: n.related_nodes || [n],
              machineRunners: runnersByMachine[n.name] || [],
            });
          }),
        )
      : null,
  );
}

export default MachinesTab;
