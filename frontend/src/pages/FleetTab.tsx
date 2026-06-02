/**
 * FleetTab.tsx — the full "Fleet / Overview" tab, extracted (behaviour 1:1)
 * from the legacy `App.tsx` monolith as part of the decomposition epic
 * (#836, pass 12 — the last large inline tab).
 *
 * Owns the fleet-status hero panel, the KPI stat row, the deployment-drift
 * banner + build note, the Machine Health table, and the Runner Fleet section
 * (status filters, fleet-wide start/stop/scale controls, the mobile runner
 * cards, and the per-machine collapsible runner tables).
 *
 * Presentational: all fleet data + handlers are owned by the legacy App and
 * threaded in as props (runners, stats, watchdog, queue, machinesData,
 * deployment, runnerAudit, system, runs, loading, and the on* callbacks),
 * exactly mirroring the legacy call site. Pure rollup logic lives in
 * `lib/fleetAlerts`, telemetry/formatting helpers in `lib/fleetTelemetry`, and
 * machine-name parsing in `lib/fleetMachines`. Glyphs come from `decompIcons`,
 * sort headers from `pages/decompSortTh`, and the stat/collapse primitives
 * from `components/*`.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 1:1 port of dynamically-typed legacy fleet/runner/node payloads; the backend response shapes lack complete TypeScript definitions. */
import React from "react";
import { legacyFetch } from "../lib/api";
import * as fleetAlerts from "../lib/fleetAlerts";
import {
  canonicalMachineName,
  offlineReasonLabel,
  parseRunnerName,
  resolveVisibility,
  runnerSort,
} from "../lib/fleetMachines";
import {
  compactRunnerActivity,
  cpuColor,
  machineTelemetryForRunner,
  runnerCurrentRun,
  shortSha,
  timeAgo,
} from "../lib/fleetTelemetry";
import { Badge } from "../primitives/Badge";
import { Pill } from "../primitives/Pill";
import { Collapse } from "../components/Collapse";
import { Stat } from "../components/Stat";
import { SortTh } from "./decompSortTh";
import { sortRows, type SortState } from "./decompSort";
import {
  ArrowDownGlyph,
  ArrowUpGlyph,
  PlayGlyph,
  ServerGlyph,
  StopGlyph,
} from "./decompIcons";

// Loosely-typed `createElement` alias: this file is a 1:1 port of the legacy
// `h()`-tree, which threads children as trailing varargs even for components
// whose typed props mark `children` required. The cast preserves that calling
// convention without per-call children plumbing.
const h: (type: any, props?: any, ...children: any[]) => React.ReactElement =
  React.createElement as any;

export function FleetTab(p: any): React.ReactElement {
  const runners = p.runners,
    stats = p.stats;
  const watchdog = p.watchdog || {};
  const queue = p.queue || {};
  const machinesData = p.machinesData || {};
  const deployment = p.deployment || {};
  const onOpenDeployment = p.onOpenDeployment || function () {};
  // Used by the fleet-status hero panel for KPI buttons and the
  // hosted-runner billing alert. Defaulted so unit tests / standalone
  // rendering of FleetTab don't need to pass them.
  const setTab = p.setTab || function () {};
  const runnerAudit = p.runnerAudit || { violations: [] };
  const driftState = React.useState<any>(null);
  const driftInfo = driftState[0], setDriftInfo = driftState[1];
  React.useEffect(function () {
    legacyFetch("/api/deployment/git-drift")
      .then(function (r: any) { return r.json(); })
      .then(function (d: any) { setDriftInfo(d); })
      .catch(function () {});
    // eslint-disable-next-line react-hooks/exhaustive-deps -- legacy 1:1: fetch drift once on mount; the useState setter is stable.
  }, []);
  const filterState = React.useState("all");
  const filter = filterState[0],
    setFilter = filterState[1];
  const expandedState = React.useState<any>({});
  const expanded = expandedState[0],
    setExpanded = expandedState[1];
  const machineSortState = React.useState<SortState>({ key: "machine", dir: "asc" });
  const machineSort = machineSortState[0],
    setMachineSort = machineSortState[1];
  const runnerTableSortState = React.useState<SortState>({ key: "number", dir: "asc" });
  const runnerTableSort = runnerTableSortState[0],
    setRunnerTableSort = runnerTableSortState[1];
  const on = runners.filter(function (r: any) {
    return r.status === "online";
  }).length;
  const busy = runners.filter(function (r: any) {
    return r.busy;
  }).length;
  const offline = runners.filter(function (r: any) {
    return r.status !== "online";
  }).length;
  const onlineIdle = runners.filter(function (r: any) {
    return r.status === "online" && !r.busy;
  }).length;
  const runnersByMachine: any = {};
  runners.forEach(function (r: any) {
    const machine = parseRunnerName(r.name).machine;
    if (!runnersByMachine[machine]) runnersByMachine[machine] = [];
    runnersByMachine[machine].push(r);
  });
  Object.keys(runnersByMachine).forEach(function (name) {
    runnersByMachine[name] = runnersByMachine[name]
      .slice()
      .sort(runnerSort);
  });
  const machineNames = Object.keys(runnersByMachine).sort(function (a, b) {
    if (a === "ControlTower") return -1;
    if (b === "ControlTower") return 1;
    return a.localeCompare(b);
  });
  const nodesByName: any = {};
  (machinesData.nodes || []).forEach(function (n: any) {
    nodesByName[canonicalMachineName(n.name).toLowerCase()] = n;
  });
  const machineNodes = machineNames.map(function (name) {
    const node = nodesByName[name.toLowerCase()];
    const mrs = runnersByMachine[name] || [];
    const onlineRunners = mrs.filter(function (r: any) {
      return r.status === "online";
    }).length;
    if (node) return Object.assign({}, node, { name: name });
    return {
      name: name,
      online: onlineRunners > 0,
      dashboard_reachable: false,
      role: "node",
      system: {},
      health: { runners_registered: mrs.length },
      last_seen: null,
      offline_reason:
        onlineRunners > 0
          ? "dashboard_not_deployed"
          : "runner_service_offline",
      offline_detail:
        onlineRunners > 0
          ? "Runner registrations are online, but the machine dashboard is not reachable for WSL/system metrics."
          : "No online runners or dashboard telemetry are visible for this machine.",
    };
  });
  (machinesData.nodes || []).forEach(function (n: any) {
    const known = machineNodes.some(function (m: any) {
      return m.name.toLowerCase() === (n.name || "").toLowerCase();
    });
    if (!known) machineNodes.push(n);
  });
  const machineAccessors = {
    machine: function (n: any) {
      return n.name;
    },
    reachability: function (n: any) {
      return n.online ? 1 : 0;
    },
    runners: function (n: any) {
      return (runnersByMachine[n.name] || []).filter(function (r: any) {
        return r.status === "online";
      }).length;
    },
    detail: function (n: any) {
      return offlineReasonLabel(n.offline_reason || (n.online ? "" : "unknown"));
    },
    resources: function (n: any) {
      return ((n.system || {}).cpu || {}).percent_1m_avg || ((n.system || {}).cpu || {}).percent || 0;
    },
    lastSeen: function (n: any) {
      return n.last_seen || "";
    },
  };
  const sortedMachineNodes = sortRows(
    machineNodes,
    machineSort,
    machineAccessors,
  );
  const runnerAccessors = {
    number: function (r: any) {
      return parseRunnerName(r.name).number;
    },
    runner: function (r: any) {
      return r.name;
    },
    state: function (r: any) {
      return r.busy ? "busy" : r.status;
    },
    labels: function (r: any) {
      return (r.labels || [])
        .map(function (l: any) {
          return l.name || l;
        })
        .join(", ");
    },
  };
  const machineCount = machineNodes.length;
  const machineOnline = machineNodes.filter(function (n: any) {
    return n.online;
  }).length;
  const queued =
    stats.queued != null ? stats.queued : queue.queued_count || 0;
  const running =
    stats.in_progress != null
      ? stats.in_progress
      : queue.in_progress_count || 0;
  const openPrs = stats.org_open_prs != null ? stats.org_open_prs : "-";
  const openIssues =
    stats.org_open_issues != null ? stats.org_open_issues : "-";
  const completedRuns = stats.runs_completed || 0;
  const localDisk = (p.system || {}).disk || {};
  const diskPressure = localDisk.pressure || {};
  const diskStatus = diskPressure.status || "unknown";
  const diskClass =
    diskStatus === "critical"
      ? "storage-critical"
      : diskStatus === "warning"
        ? "storage-warning"
        : "";
  const filteredRunners = runners.filter(function (r: any) {
    if (filter === "online") return r.status === "online" && !r.busy;
    if (filter === "busy") return r.busy;
    if (filter === "offline") return r.status !== "online";
    return true;
  });
  const visibleIds: any = {};
  filteredRunners.forEach(function (r: any) {
    visibleIds[r.id] = true;
  });
  function toggleMachine(name: string) {
    setExpanded(
      Object.assign({}, expanded, {
        [name]: expanded[name] === false ? true : false,
      }),
    );
  }
  // ─── Fleet status hero panel (computed once per render) ─────────────────
  // The rollup logic lives in frontend/src/lib/fleetAlerts.ts so it can be
  // unit-tested without the legacy h()-tree. The hero panel below is the
  // only consumer today; the new shell migration will reuse the same fn.
  const heroResult = fleetAlerts.computeFleetAlerts({
    machineCount: machineCount,
    machineOnline: machineOnline,
    machineNodes: machineNodes,
    watchdog: watchdog,
    stats: stats,
    completedRuns: completedRuns,
    runnerAudit: runnerAudit,
  });
  const heroAlerts = heroResult.alerts;
  const heroLevel = heroResult.level;
  const heroLevelLabel = fleetAlerts.fleetLevelLabel(heroLevel);
  const heroLevelColor = heroLevel === "ok"
    ? "var(--accent-green)"
    : heroLevel === "warning"
      ? "var(--accent-yellow)"
      : "var(--accent-red)";

  return h(
    "div",
    null,
    h(
      "section",
      {
        className: "fleet-hero fleet-hero--" + heroLevel,
        role: "region",
        "aria-label": "Fleet status",
      },
      h(
        "div",
        { className: "fleet-hero__status" },
        h("span", {
          className: "fleet-hero__dot",
          style: { background: heroLevelColor, boxShadow: "0 0 0 4px " + heroLevelColor.replace(")", " / 0.18)").replace("var(--", "var(--") },
          "aria-hidden": true,
        }),
        h(
          "div",
          { className: "fleet-hero__status-text" },
          h("div", { className: "fleet-hero__title" }, "Fleet ", heroLevelLabel),
          h("div", { className: "fleet-hero__subtitle" },
            heroAlerts.length === 0
              ? "All systems nominal"
              : heroAlerts.length + " active alert" + (heroAlerts.length === 1 ? "" : "s")),
        ),
      ),
      h(
        "div",
        { className: "fleet-hero__kpis" },
        h("button", { className: "fleet-hero__kpi", onClick: function () { setTab("machines"); }, type: "button" },
          h("span", { className: "fleet-hero__kpi-label" }, "Machines"),
          h("span", { className: "fleet-hero__kpi-value" }, machineOnline + " / " + machineCount),
        ),
        h("button", { className: "fleet-hero__kpi", onClick: function () { setTab("overview"); }, type: "button" },
          h("span", { className: "fleet-hero__kpi-label" }, "Open PRs"),
          h("span", { className: "fleet-hero__kpi-value" }, String(openPrs)),
        ),
        h("button", { className: "fleet-hero__kpi", onClick: function () { setTab("queue"); }, type: "button" },
          h("span", { className: "fleet-hero__kpi-label" }, "Queue"),
          h("span", { className: "fleet-hero__kpi-value" }, String(queued)),
          queued > 0
            ? h("span", { className: "fleet-hero__kpi-sub" }, running + " running")
            : null,
        ),
        h("button", { className: "fleet-hero__kpi", onClick: function () { setTab("overview"); }, type: "button" },
          h("span", { className: "fleet-hero__kpi-label" }, "Runners"),
          h("span", { className: "fleet-hero__kpi-value" }, on + " / " + runners.length),
          busy > 0
            ? h("span", { className: "fleet-hero__kpi-sub" }, busy + " busy")
            : null,
        ),
      ),
      h(
        "div",
        { className: "fleet-hero__alerts", "aria-label": "Fleet alerts" },
        heroAlerts.length === 0
          ? h("span", { className: "fleet-hero__alert fleet-hero__alert--ok" }, "No active fleet alerts")
          : heroAlerts.slice(0, 3).map(function (alert: any) {
              return h(
                "div",
                { key: alert.id, className: "fleet-hero__alert fleet-hero__alert--" + alert.level },
                h("span", { className: "fleet-hero__alert-title" }, alert.title),
                h("span", { className: "fleet-hero__alert-detail" }, alert.detail),
              );
            }),
      ),
    ),
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Runners Online",
        value: on + "/" + runners.length,
        color:
          on === runners.length
            ? "var(--accent-green)"
            : "var(--accent-yellow)",
        sub: busy + " busy",
      }),
      h(Stat, {
        label: "Machines Online",
        value: machineOnline + "/" + machineCount,
        color:
          machineCount > 0 && machineOnline === machineCount
            ? "var(--accent-green)"
            : "var(--accent-yellow)",
        sub:
          machineNodes
            .filter(function (n: any) {
              return (
                resolveVisibility(
                  n,
                  (n.health && n.health.runners_registered) || 0,
                ).state !== "full_telemetry"
              );
            })
            .map(function (n: any) {
              const vis = resolveVisibility(
                n,
                (n.health && n.health.runners_registered) || 0,
              );
              return n.name + ": " + vis.label;
            })
            .join("; ") || "all nodes fully visible",
      }),
      h(Stat, {
        label: "WSL Keepalive",
        value:
          watchdog.status === "healthy"
            ? "Healthy"
            : watchdog.status === "legacy"
              ? "Legacy VBS"
              : watchdog.status === "degraded"
                ? "Needs attention"
                : "Unknown",
        color:
          watchdog.status === "healthy"
            ? "var(--accent-green)"
            : watchdog.status === "legacy"
              ? "var(--accent-red)"
              : watchdog.status === "degraded"
                ? "var(--accent-yellow)"
                : "inherit",
        // Truncate the keepalive detail so a 200-character Windows
        // scheduled-task error doesn't blow out the card height. Full
        // detail stays available via tooltip + the alerts hero up top.
        sub:
          watchdog.summary
            ? (watchdog.summary.length > 80
                ? watchdog.summary.slice(0, 77) + "…"
                : watchdog.summary)
            : "Read-only keepalive checks",
        subTitle: watchdog.detail || watchdog.summary || "",
      }),
      h(Stat, {
        label: "Storage",
        value:
          localDisk.free_gb != null ? localDisk.free_gb + " GB" : "-",
        color:
          diskStatus === "critical"
            ? "var(--accent-red)"
            : diskStatus === "warning"
              ? "var(--accent-yellow)"
              : "var(--accent-green)",
        sub:
          localDisk.percent != null
            ? localDisk.percent + "% used on " + (localDisk.path || "/")
            : "disk telemetry",
      }),
      h(Stat, {
        label: "Success Rate",
        value:
          stats.success_rate !== undefined
            ? stats.success_rate + "%"
            : "-",
        color:
          stats.success_rate >= 90
            ? "var(--accent-green)"
            : stats.success_rate >= 70
              ? "var(--accent-yellow)"
              : "var(--accent-red)",
        sub: completedRuns
          ? stats.runs_success +
            "/" +
            completedRuns +
            " recent completed runs passed"
          : "",
      }),
      h(Stat, {
        label: "Open PRs",
        value: openPrs,
        sub: "across org",
      }),
      h(Stat, {
        label: "Open Issues",
        value: openIssues,
        sub: "excluding PRs",
      }),
      h(Stat, {
        label: "Workflow Queue",
        value: queued,
        color: queued > 0 ? "var(--accent-yellow)" : "inherit",
        sub: "waiting for runners",
      }),
      h(Stat, {
        label: "Running Workflows",
        value: running,
        color: running > 0 ? "var(--accent-yellow)" : "inherit",
        sub: "in progress now",
      }),
    ),
    driftInfo && driftInfo.is_drifted
      ? h(
          "div",
          {
            style: {
              background: "rgba(210,153,34,0.12)",
              border: "1px solid var(--accent-yellow)",
              borderRadius: 6,
              padding: "8px 14px",
              marginBottom: 10,
              fontSize: 12,
              color: "var(--accent-yellow)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            },
          },
          "⚠️ Deployed version is behind origin/main. Run update-deployed.sh to update.",
          h(
            "span",
            { style: { opacity: 0.8, marginLeft: 4 } },
            "(local: " + (driftInfo.source_commit || "?") + " → remote: " + (driftInfo.remote_commit || "?") + ")",
          ),
        )
      : null,
    h(
      "div",
      { className: "deployment-note" },
      h("span", null, "Dashboard build"),
      h(
        "code",
        {
          title:
            (deployment.git_branch || "unknown") +
            " " +
            (deployment.git_sha || "unknown") +
            (deployment.deployed_at
              ? " deployed " + deployment.deployed_at
              : ""),
        },
        (deployment.git_branch || "unknown") +
          "@" +
          shortSha(deployment.git_sha),
      ),
      h(
        "button",
        {
          className: "btn",
          style: { padding: "0 8px", fontSize: 11, height: 22 },
          onClick: onOpenDeployment,
        },
        "Deployment state",
      ),
      deployment.git_dirty
        ? h("span", { className: "storage-warning" }, "local changes")
        : null,
      diskStatus !== "healthy" && diskStatus !== "unknown"
        ? h(
            "span",
            { className: diskClass },
            "Storage " +
              diskStatus +
              ": " +
              localDisk.free_gb +
              " GB free",
          )
        : null,
    ),
    h(
      Collapse,
      {
        title: "Machine Health",
        icon: h(ServerGlyph, { size: 16 }),
        badge: machineOnline + "/" + machineCount + " online",
        defaultOpen: true,
      },
      h(
        "table",
        { className: "data-table", style: { width: "100%" } },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h(SortTh, {
              label: "Machine",
              sortKey: "machine",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Reachability",
              sortKey: "reachability",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Runners",
              sortKey: "runners",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Offline Detail",
              sortKey: "detail",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Resources",
              sortKey: "resources",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Last Seen",
              sortKey: "lastSeen",
              sort: machineSort,
              setSort: setMachineSort,
            }),
          ),
        ),
        h(
          "tbody",
          null,
          sortedMachineNodes.map(function (n: any) {
            const mrs = runnersByMachine[n.name] || [];
            const onlineRunners = mrs.filter(function (r: any) {
              return r.status === "online";
            }).length;
            const sys = n.system || {};
            const cpu = sys.cpu || {};
            const mem = sys.memory || {};
            const disk = sys.disk || {};
            const reason =
              n.offline_reason ||
              (!n.dashboard_reachable && onlineRunners > 0
                ? "dashboard_not_deployed"
                : !n.online
                  ? "unknown"
                  : null);
            return h(
              "tr",
              { key: n.name },
              h("td", null, h("strong", null, n.name)),
              h(
                "td",
                null,
                h(
                  Badge,
                  { tone: n.online ? "success" : "danger" },
                  n.online ? "online" : "offline",
                ),
              ),
              h("td", null, onlineRunners + "/" + mrs.length + " online"),
              h(
                "td",
                null,
                reason
                  ? h(
                      "span",
                      {
                        title: n.offline_detail || n.error || "",
                        style: {
                          color:
                            reason === "resource_monitoring"
                              ? "var(--accent-yellow)"
                              : "var(--accent-red)",
                        },
                      },
                      offlineReasonLabel(reason),
                    )
                  : h(
                      "span",
                      { style: { color: "var(--accent-green)" } },
                      "Healthy",
                    ),
              ),
              h(
                "td",
                null,
                sys.uptime_seconds
                  ? "CPU " +
                      (cpu.percent_1m_avg || cpu.percent || 0) +
                      "% · RAM " +
                      (mem.total_gb ? Math.round((1 - mem.available_gb / mem.total_gb) * 100) : Math.round(mem.percent || 0)) +
                      "% · Disk " +
                      ((disk.windows_host || disk).percent || 0) +
                      "%"
                  : "No telemetry",
              ),
              h(
                "td",
                null,
                n.last_seen ? timeAgo(n.last_seen) : "not seen",
              ),
            );
          }),
        ),
      ),
    ),
    h(
      Collapse,
      {
        title: "Runner Fleet",
        icon: h(ServerGlyph, { size: 16 }),
        badge: on + "/" + runners.length + " online",
        defaultOpen: true,
      },
      h(
        "div",
        {
          className: "fleet-controls",
          style: {
            marginBottom: 12,
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
          },
        },
        h(
          "div",
          {
            className: "fleet-mobile-kpis",
            style: { flexBasis: "100%" },
          },
          [
            { label: "Total", value: runners.length },
            { label: "Online", value: on },
            { label: "Busy", value: busy },
            { label: "Offline", value: offline },
          ].map(function (item) {
            return h(
              "div",
              { key: item.label, className: "fleet-mobile-kpi" },
              h("div", { className: "fleet-mobile-kpi-label" }, item.label),
              h("div", { className: "fleet-mobile-kpi-value" }, item.value),
            );
          }),
        ),
        h(
          "div",
          {
            className: "fleet-status-strip",
            role: "group",
            "aria-label": "Runner status filters",
            style: { flexBasis: "100%" },
          },
          [
            { key: "all", label: "All", count: runners.length, dot: "green" },
            { key: "online", label: "Online", count: onlineIdle, dot: "green" },
            { key: "busy", label: "Busy", count: busy, dot: "yellow" },
            { key: "offline", label: "Offline", count: offline, dot: "red" },
          ].map(function (item) {
            return h(
              Pill,
              {
                key: item.key,
                className: "fleet-status-pill",
                onClick: function () {
                  setFilter(item.key);
                },
                selected: filter === item.key,
              },
              h("span", { className: "status-dot " + item.dot }),
              item.label,
              h(Badge, { tone: "neutral" }, item.count),
            );
          }),
        ),
        h(
          "button",
          {
            className: "btn btn-green",
            onClick: function () {
              p.onFleet("all-up");
            },
            disabled: p.loading,
          },
          h(PlayGlyph, { size: 12 }),
          " Start All",
        ),
        h(
          "button",
          {
            className: "btn btn-red",
            onClick: function () {
              p.onFleet("all-down");
            },
            disabled: p.loading,
          },
          h(StopGlyph, { size: 12 }),
          " Stop All",
        ),
        h(
          "button",
          {
            className: "btn",
            onClick: function () {
              p.onFleet("up");
            },
            disabled: p.loading,
          },
          h(ArrowUpGlyph, { size: 12 }),
          " Scale Up",
        ),
        h(
          "button",
          {
            className: "btn",
            onClick: function () {
              p.onFleet("down");
            },
            disabled: p.loading,
          },
          h(ArrowDownGlyph, { size: 12 }),
          " Scale Down",
        ),
        p.loading ? h("span", { className: "spinner" }) : null,
        h(
          "span",
          { style: { color: "var(--text-muted)", fontSize: 12 } },
          "Filter:",
        ),
        ["all", "online", "busy", "offline"].map(function (name) {
          return h(
            "button",
            {
              key: name,
              className: "btn" + (filter === name ? " btn-green" : ""),
              onClick: function () {
                setFilter(name);
              },
            },
            name.charAt(0).toUpperCase() + name.slice(1),
          );
        }),
      ),
      h(
        "div",
        {
          className: "fleet-mobile-runner-list",
          "aria-label": "Mobile runner monitoring cards",
        },
        filteredRunners
          .slice()
          .sort(runnerSort)
          .map(function (r: any) {
            const parsed = parseRunnerName(r.name);
            const telemetry = machineTelemetryForRunner(r, nodesByName);
            const currentRun = runnerCurrentRun(r, p.runs || []);
            const state = r.busy ? "busy" : r.status;
            return h(
              "div",
              { key: r.id, className: "mobile-runner-card" },
              h(
                "div",
                { className: "mobile-runner-card-header" },
                h(
                  "div",
                  null,
                  h("div", { className: "mobile-runner-card-title" }, r.name),
                  h(
                    "div",
                    { className: "mobile-runner-card-meta" },
                    h("span", null, telemetry.machine + " #" + parsed.number),
                    h("span", null, compactRunnerActivity(currentRun)),
                    h("span", null, telemetry.seen),
                  ),
                ),
                h(
                  "span",
                  { className: "runner-status-badge " + state },
                  state,
                ),
              ),
              h(
                "div",
                { className: "mobile-runner-meter-row" },
                [
                  { label: "CPU", value: telemetry.cpu },
                  { label: "RAM", value: telemetry.memory },
                ].map(function (meter) {
                  return h(
                    "div",
                    { key: meter.label, className: "mobile-runner-meter" },
                    h(
                      "div",
                      { className: "mobile-runner-meter-label" },
                      h("span", null, meter.label),
                      h("span", null, meter.value + "%"),
                    ),
                    h(
                      "div",
                      { className: "mobile-runner-meter-track" },
                      h("div", {
                        className: "mobile-runner-meter-fill",
                        style: {
                          width: meter.value + "%",
                          background: cpuColor(meter.value),
                        },
                      }),
                    ),
                  );
                }),
              ),
              h(
                "div",
                { className: "mobile-runner-card-meta" },
                h("span", null, "uptime " + telemetry.uptime),
                h(
                  "span",
                  null,
                  telemetry.node.dashboard_reachable === false
                    ? "runners only"
                    : "dashboard live",
                ),
              ),
            );
          }),
      ),
      h(
        "div",
        { className: "runner-fleet-desktop-list", style: { display: "grid", gap: 10 } },
        machineNames.map(function (machine) {
          const machineRunners = (runnersByMachine[machine] || []).filter(
            function (runner: any) {
              return visibleIds[runner.id];
            },
          );
          if (!machineRunners.length) return null;
          const sortedMachineRunners = sortRows(
            machineRunners,
            runnerTableSort,
            runnerAccessors,
          );
          const node = nodesByName[machine.toLowerCase()] || {};
          const sys = node.system || {};
          const cpu = sys.cpu || {};
          const mem = sys.memory || {};
          const onlineCount = machineRunners.filter(function (r: any) {
            return r.status === "online";
          }).length;
          const busyCount = machineRunners.filter(function (r: any) {
            return r.busy;
          }).length;
          const open = expanded[machine] !== false;
          const deploy =
            node.health && node.health.deployment
              ? node.health.deployment.git_sha
              : "";
          const stale =
            deploy && deployment.git_sha && deploy !== deployment.git_sha;
          return h(
            "div",
            {
              key: machine,
              className: "card",
              style: { padding: 0, overflow: "hidden" },
            },
            h(
              "button",
              {
                className: "btn",
                onClick: function () {
                  toggleMachine(machine);
                },
                style: {
                  width: "100%",
                  justifyContent: "space-between",
                  border: "none",
                  borderRadius: 0,
                  padding: "12px 14px",
                  background: "var(--bg-secondary)",
                },
              },
              h(
                "span",
                {
                  style: {
                    display: "flex",
                    gap: 10,
                    alignItems: "center",
                  },
                },
                h("span", {
                  className:
                    "status-dot " + (onlineCount > 0 ? "green" : "red"),
                }),
                h("strong", null, machine),
                h(
                  "span",
                  { className: "section-badge" },
                  onlineCount +
                    "/" +
                    machineRunners.length +
                    " online, " +
                    busyCount +
                    " busy",
                ),
              ),
              h(
                "span",
                { style: { color: "var(--text-muted)", fontSize: 12 } },
                "CPU " +
                  Math.round(cpu.percent_1m_avg || cpu.percent || 0) +
                  "% | RAM " +
                  (mem.total_gb ? Math.round((1 - mem.available_gb / mem.total_gb) * 100) : Math.round(mem.percent || 0)) +
                  "% | " +
                  (node.dashboard_reachable === false
                    ? "runners only"
                    : "dashboard live") +
                  (stale ? " | stale build" : ""),
              ),
            ),
            open
              ? h(
                  "table",
                  { className: "data-table", style: { width: "100%" } },
                  h(
                    "thead",
                    null,
                    h(
                      "tr",
                      null,
                      h(SortTh, {
                        label: "#",
                        sortKey: "number",
                        sort: runnerTableSort,
                        setSort: setRunnerTableSort,
                        thProps: { style: { width: 54 } },
                      }),
                      h(SortTh, {
                        label: "Runner",
                        sortKey: "runner",
                        sort: runnerTableSort,
                        setSort: setRunnerTableSort,
                      }),
                      h(SortTh, {
                        label: "State",
                        sortKey: "state",
                        sort: runnerTableSort,
                        setSort: setRunnerTableSort,
                      }),
                      h(SortTh, {
                        label: "Labels",
                        sortKey: "labels",
                        sort: runnerTableSort,
                        setSort: setRunnerTableSort,
                      }),
                      h("th", { style: { width: 90 } }, ""),
                    ),
                  ),
                  h(
                    "tbody",
                    null,
                    sortedMachineRunners.map(function (r: any) {
                      const parsed = parseRunnerName(r.name);
                      const st = r.busy ? "busy" : r.status;
                      const customLabels = (r.labels || [])
                        .filter(function (l: any) {
                          const n = l.name || l;
                          return (
                            n !== "self-hosted" &&
                            n !== "Linux" &&
                            n !== "X64" &&
                            !n.startsWith("d-sorg-fleet")
                          );
                        })
                        .map(function (l: any) {
                          return l.name || l;
                        });
                      return h(
                        "tr",
                        { key: r.id },
                        h("td", null, parsed.number),
                        h("td", null, r.name),
                        h(
                          "td",
                          null,
                          h(
                            "span",
                            { className: "runner-status-badge " + st },
                            st,
                          ),
                        ),
                        h(
                          "td",
                          null,
                          customLabels.length
                            ? customLabels.slice(0, 3).join(", ")
                            : "-",
                        ),
                        h(
                          "td",
                          null,
                          h(
                            "button",
                            {
                              className:
                                r.status === "online"
                                  ? "btn btn-red"
                                  : "btn btn-green",
                              style: { padding: "2px 8px", fontSize: 11 },
                              onClick: function () {
                                p.onRunner(
                                  r.id,
                                  r.status === "online"
                                    ? "stop"
                                    : "start",
                                );
                              },
                              disabled: p.loading,
                            },
                            r.status === "online" ? "Stop" : "Start",
                          ),
                        ),
                      );
                    }),
                  ),
                )
              : null,
          );
        }),
      ),
    ),
  );
}
