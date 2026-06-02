// @ts-nocheck
/* eslint-disable */
// NOTE: This directive MUST stay on line 1 — TypeScript only honors
// `@ts-nocheck` when it is the first comment in the file (before any
// statements). It was previously placed after the imports, so it was
// silently ignored and the legacy file leaked ~1000 type errors into any
// `tsc` run. Decomposition of this file is tracked separately (#403).
import React from "react"
import { legacyFetch } from "../lib/api"
import * as fleetAlerts from "../lib/fleetAlerts"
import { AgentDispatchPage } from "../pages/AgentDispatch"
import { Conductor } from "../pages/Conductor"
import { QueueTab } from "../pages/Queue"
import { LinearSetup } from "../pages/LinearSetup"
import PushSettings from "../pages/PushSettings"
import { ClineLauncherTab } from "../pages/ClineLauncher"
import { DiagnosticsTab } from "../pages/Diagnostics"
import { PrincipalsTab } from "../pages/Principals"
import RunnerAudit from "../pages/RunnerAudit"
import ScheduledJobs from "../pages/ScheduledJobs"
import { OrgTab } from "../pages/Org"
import { TestsTab } from "../pages/Tests"
import { LocalAppsTab } from "../pages/LocalApps"
import { WorkflowsTab } from "../pages/Workflows"
import { AssessmentsTab } from "../pages/Assessments"
import { DeploymentTab } from "../pages/Deployment"
import { FleetOrchestrationTab } from "../pages/FleetOrchestration"
import { CredentialsTab } from "../pages/CredentialsPage"
import { MaxwellTab } from "../pages/MaxwellPage"
import { RunnerScheduleTab } from "../pages/RunnerSchedule"
import { FeatureRequestsTab } from "../pages/FeatureRequests"
import { StatsTab, PerformanceTab, AnalysisOutcomesTab, ReportsTab } from "../pages/Analysis"
import { Badge } from "../primitives/Badge"
import { Pill } from "../primitives/Pill"
import { AlertsCenter } from "../primitives/AlertsCenter"
import { EventsTab, OverviewEventSection } from "../pages/Events"
import { RemediationTab } from "../pages/RemediationTab"
import { Collapse } from "../components/Collapse"
import { SubTabs } from "../components/SubTabs"
import { Stat } from "../components/Stat"
import { SortTh } from "../pages/decompSortTh"
import { sortRows } from "../pages/decompSort"
import { useFleetEvents } from "../hooks/useFleetEvents"
import { RecoveryDialog } from "./RecoveryDialog"
import { SessionExpiredDialog } from "./SessionExpiredDialog"
import { marked } from "marked"
import DOMPurify from "dompurify"
import {
  emitSessionExpired,
  shouldIgnoreUnauthorizedResponse,
  subscribeSessionExpired,
  tryRefreshSession,
} from "./sessionExpired"
import { installWheelValueGuard } from "./wheelValueGuard"
import { ThemeSettings } from "../components/ThemeSettings"
import { AssistantSidebar, DashboardHelp } from "../pages/AssistantSidebar"
import { QuickDispatchPopover } from "../pages/QuickDispatch"
import { MachinesTab } from "../pages/Machines"
import { HistoryTab } from "../pages/History"
import {
  canonicalMachineName,
  nodeQualityScore,
  offlineReasonLabel,
  parseRunnerName,
  resolveVisibility,
  runnerSort,
} from "../lib/fleetMachines"
import {
  ASST_LS,
  clearAssistantTranscriptHistory,
  lsGet,
  lsSet,
} from "../lib/assistantStorage"

var h = React.createElement;
var SERVICE_WORKER_CACHE_DENYLIST = [/^\/api\/credentials(?:\/|$)/];

function shouldBypassServiceWorkerCache(url) {
  try {
    var parsed = new URL(url, window.location.origin);
    return SERVICE_WORKER_CACHE_DENYLIST.some(function (pattern) {
      return pattern.test(parsed.pathname);
    });
  } catch (e) {
    return false;
  }
}

// Wrap global fetch to detect session-expiry 401s and prompt login through React.
var originalFetch = window.fetch;
window.fetch = async function(url, opts) {
  if (shouldBypassServiceWorkerCache(url)) {
    opts = Object.assign({}, opts || {}, { cache: "no-store" });
  }
  var resp = await originalFetch(url, opts);
  if (resp.status === 401 && !shouldIgnoreUnauthorizedResponse(url)) {
    console.warn("[auth] 401 Unauthorized from", url);
    if (await tryRefreshSession(originalFetch)) {
      return originalFetch(url, opts);
    }
    // Emit a global toast so the announcement is screen-reader-accessible
    // even before the modal repaints (issue #421).
    try {
      var toaster = (window as any).__toaster;
      if (toaster && typeof toaster.showToast === "function") {
        toaster.showToast(
          "Your session has expired. Please log in again to continue.",
          { variant: "error", title: "Session expired" },
        );
      }
    } catch (toastErr) {
      console.warn("[auth] Failed to emit 401 toast:", toastErr);
    }
    emitSessionExpired();
  }
  return resp;
};
// ────────────────────────────────────────────────────────────────────────

// Configure marked with safe options (issue #7)
if (typeof marked !== "undefined") {
  marked.use({ mangle: false, headerIds: false, gfm: true });
}

/**
 * safeOpen – open a URL in a new tab only when it belongs to a trusted
 * origin (issue #30).  Blocks arbitrary URLs that could be injected via
 * API responses.
 * @param {string} url
 */
function safeOpen(url) {
  if (
    !url.startsWith("http://localhost") &&
    !url.startsWith("https://github.com/") &&
    !url.startsWith("https://api.github.com/")
  ) {
    console.error("Blocked unsafe URL:", url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

var LANG_COLORS = {
  JavaScript: "#f1e05a",
  TypeScript: "#3178c6",
  Python: "#3572A5",
  Rust: "#dea584",
  Go: "#00ADD8",
  Java: "#b07219",
  C: "#555555",
  "C++": "#f34b7d",
  "C#": "#178600",
  Ruby: "#701516",
  Shell: "#89e051",
  HTML: "#e34c26",
  CSS: "#563d7c",
  MATLAB: "#e16737",
  Jupyter: "#DA5B0B",
  Vue: "#41b883",
  Swift: "#F05138",
  Kotlin: "#A97BFF",
  Dart: "#00B4AB",
};

function icon(path, s) {
  s = s || 16;
  return h(
    "svg",
    {
      width: s,
      height: s,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    },
    h("path", { d: path }),
  );
}

var I = {
  chevronDown: function (s) {
    return icon("M6 9l6 6 6-6", s);
  },
  server: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("rect", { x: 2, y: 2, width: 20, height: 8, rx: 2 }),
      h("rect", { x: 2, y: 14, width: 20, height: 8, rx: 2 }),
      h("circle", { cx: 6, cy: 6, r: 1, fill: "currentColor" }),
      h("circle", { cx: 6, cy: 18, r: 1, fill: "currentColor" }),
    );
  },
  cpu: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("rect", { x: 4, y: 4, width: 16, height: 16, rx: 2 }),
      h("rect", { x: 9, y: 9, width: 6, height: 6 }),
      h("path", {
        d: "M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3",
      }),
    );
  },
  activity: function (s) {
    return icon("M22 12h-4l-3 9L9 3l-3 9H2", s);
  },
  gitPR: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 18, cy: 18, r: 3 }),
      h("circle", { cx: 6, cy: 6, r: 3 }),
      h("path", { d: "M13 6h3a2 2 0 012 2v7M6 9v12" }),
    );
  },
  issue: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 12, cy: 12, r: 10 }),
      h("line", { x1: 12, y1: 8, x2: 12, y2: 12 }),
      h("line", { x1: 12, y1: 16, x2: 12.01, y2: 16 }),
    );
  },
  settings: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 12, cy: 12, r: 3 }),
      h("path", {
        d: "M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1 1.54V21a2 2 0 11-4 0v-.09a1.7 1.7 0 00-1-1.54 1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.7 1.7 0 00.34-1.87 1.7 1.7 0 00-1.54-1H3a2 2 0 110-4h.09a1.7 1.7 0 001.54-1 1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06a1.7 1.7 0 001.87.34H9a1.7 1.7 0 001-1.54V3a2 2 0 114 0v.09a1.7 1.7 0 001 1.54 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06a1.7 1.7 0 00-.34 1.87V9c.25.61.85 1 1.54 1H21a2 2 0 110 4h-.09a1.7 1.7 0 00-1.54 1z",
      }),
    );
  },
  repo: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", { d: "M4 19.5A2.5 2.5 0 016.5 17H20" }),
      h("path", {
        d: "M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z",
      }),
    );
  },
  play: function (s) {
    return icon("M5 3l14 9-14 9V3z", s);
  },
  stop: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
      },
      h("rect", { x: 6, y: 6, width: 12, height: 12, rx: 1 }),
    );
  },
  arrowUp: function (s) {
    return icon("M12 19V5M5 12l7-7 7 7", s);
  },
  arrowDown: function (s) {
    return icon("M12 5v14M5 12l7 7 7-7", s);
  },
  refresh: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", { d: "M23 4v6h-6M1 20v-6h6" }),
      h("path", {
        d: "M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15",
      }),
    );
  },
  flask: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", {
        d: "M9 3h6M10 3v7.4a2 2 0 01-.5 1.3L4 19a2 2 0 001.5 3h13a2 2 0 001.5-3l-5.5-7.3a2 2 0 01-.5-1.3V3",
      }),
    );
  },
  fileText: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", {
        d: "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z",
      }),
      h("polyline", { points: "14 2 14 8 20 8" }),
      h("line", { x1: 16, y1: 13, x2: 8, y2: 13 }),
      h("line", { x1: 16, y1: 17, x2: 8, y2: 17 }),
    );
  },
  docker: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("rect", { x: 1, y: 10, width: 22, height: 10, rx: 2 }),
      h("rect", { x: 5, y: 6, width: 4, height: 4 }),
      h("rect", { x: 10, y: 6, width: 4, height: 4 }),
      h("rect", { x: 10, y: 2, width: 4, height: 4 }),
    );
  },
  queue: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("line", { x1: 8, y1: 6, x2: 21, y2: 6 }),
      h("line", { x1: 8, y1: 12, x2: 21, y2: 12 }),
      h("line", { x1: 8, y1: 18, x2: 21, y2: 18 }),
      h("circle", { cx: 3, cy: 6, r: 1, fill: "currentColor" }),
      h("circle", { cx: 3, cy: 12, r: 1, fill: "currentColor" }),
      h("circle", { cx: 3, cy: 18, r: 1, fill: "currentColor" }),
    );
  },
  clock: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 12, cy: 12, r: 10 }),
      h("polyline", { points: "12 6 12 12 16 14" }),
    );
  },
};

function timeAgo(d) {
  if (!d) return "";
  var s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}
function formatDuration(s) {
  if (!s || s < 0) return "-";
  if (s < 60) return s + "s";
  return Math.floor(s / 60) + "m " + (s % 60) + "s";
}
function formatBytes(b) {
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
  if (b < 1073741824) return (b / 1048576).toFixed(1) + " MB";
  return (b / 1073741824).toFixed(2) + " GB";
}
function pColor(p) {
  return p < 60 ? "green" : p < 85 ? "yellow" : "red";
}
function cpuColor(p) {
  return p < 30
    ? "rgba(63,185,80,0.3)"
    : p < 60
      ? "rgba(63,185,80,0.6)"
      : p < 80
        ? "rgba(210,153,34,0.6)"
        : "rgba(248,81,73,0.7)";
}

function isAnalysisTabKey(key) {
  return ["analysis", "stats", "performance", "reports", "history"].indexOf(key) >= 0;
}

function boundedPercent(value) {
  var n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function machineTelemetryForRunner(runner, nodesByName) {
  var machine = parseRunnerName(runner.name).machine;
  var node = nodesByName[machine.toLowerCase()] || {};
  var sys = node.system || {};
  var cpu = sys.cpu || {};
  var mem = sys.memory || {};
  var cpuPct = boundedPercent(cpu.percent_1m_avg || cpu.percent || 0);
  var memPct = mem.total_gb
    ? boundedPercent((1 - mem.available_gb / mem.total_gb) * 100)
    : boundedPercent(mem.percent || 0);
  return {
    machine: machine,
    node: node,
    cpu: cpuPct,
    memory: memPct,
    uptime: sys.uptime_seconds ? formatDuration(sys.uptime_seconds) : "no uptime",
    seen: node.last_seen ? timeAgo(node.last_seen) : "not seen",
  };
}

function runnerCurrentRun(runner, runs) {
  return (runs || []).find(function (run) {
    var status = String(run.status || "").toLowerCase();
    var isActive =
      status === "in_progress" ||
      status === "queued" ||
      status === "waiting" ||
      (!run.conclusion && status !== "completed");
    return (
      isActive &&
      (run.runner_name === runner.name || run.runner_id === runner.id)
    );
  });
}

function compactRunnerActivity(currentRun) {
  if (!currentRun) return "idle";
  if (currentRun.workflow_name) return currentRun.workflow_name;
  if (currentRun.name) return currentRun.name;
  if (currentRun.status) return currentRun.status;
  return "running";
}

function shortSha(sha) {
  return sha ? String(sha).slice(0, 7) : "unknown";
}

// ════════════════════════ FLEET TAB ════════════════════════
function FleetTab(p) {
  var runners = p.runners,
    stats = p.stats;
  var watchdog = p.watchdog || {};
  var queue = p.queue || {};
  var machinesData = p.machinesData || {};
  var deployment = p.deployment || {};
  var onOpenDeployment = p.onOpenDeployment || function () {};
  // Used by the fleet-status hero panel for KPI buttons and the
  // hosted-runner billing alert. Defaulted so unit tests / standalone
  // rendering of FleetTab don't need to pass them.
  var setTab = p.setTab || function () {};
  var runnerAudit = p.runnerAudit || { violations: [] };
  var driftState = React.useState(null);
  var driftInfo = driftState[0], setDriftInfo = driftState[1];
  React.useEffect(function () {
    legacyFetch("/api/deployment/git-drift")
      .then(function (r) { return r.json(); })
      .then(function (d) { setDriftInfo(d); })
      .catch(function () {});
  }, []);
  var filterState = React.useState("all");
  var filter = filterState[0],
    setFilter = filterState[1];
  var expandedState = React.useState({});
  var expanded = expandedState[0],
    setExpanded = expandedState[1];
  var machineSortState = React.useState({ key: "machine", dir: "asc" });
  var machineSort = machineSortState[0],
    setMachineSort = machineSortState[1];
  var runnerTableSortState = React.useState({ key: "number", dir: "asc" });
  var runnerTableSort = runnerTableSortState[0],
    setRunnerTableSort = runnerTableSortState[1];
  var on = runners.filter(function (r) {
    return r.status === "online";
  }).length;
  var busy = runners.filter(function (r) {
    return r.busy;
  }).length;
  var offline = runners.filter(function (r) {
    return r.status !== "online";
  }).length;
  var onlineIdle = runners.filter(function (r) {
    return r.status === "online" && !r.busy;
  }).length;
  var runnersByMachine = {};
  runners.forEach(function (r) {
    var machine = parseRunnerName(r.name).machine;
    if (!runnersByMachine[machine]) runnersByMachine[machine] = [];
    runnersByMachine[machine].push(r);
  });
  Object.keys(runnersByMachine).forEach(function (name) {
    runnersByMachine[name] = runnersByMachine[name]
      .slice()
      .sort(runnerSort);
  });
  var machineNames = Object.keys(runnersByMachine).sort(function (a, b) {
    if (a === "ControlTower") return -1;
    if (b === "ControlTower") return 1;
    return a.localeCompare(b);
  });
  var nodesByName = {};
  (machinesData.nodes || []).forEach(function (n) {
    nodesByName[canonicalMachineName(n.name).toLowerCase()] = n;
  });
  var machineNodes = machineNames.map(function (name) {
    var node = nodesByName[name.toLowerCase()];
    var mrs = runnersByMachine[name] || [];
    var onlineRunners = mrs.filter(function (r) {
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
  (machinesData.nodes || []).forEach(function (n) {
    var known = machineNodes.some(function (m) {
      return m.name.toLowerCase() === (n.name || "").toLowerCase();
    });
    if (!known) machineNodes.push(n);
  });
  var machineAccessors = {
    machine: function (n) {
      return n.name;
    },
    reachability: function (n) {
      return n.online ? 1 : 0;
    },
    runners: function (n) {
      return (runnersByMachine[n.name] || []).filter(function (r) {
        return r.status === "online";
      }).length;
    },
    detail: function (n) {
      return offlineReasonLabel(n.offline_reason || (n.online ? "" : "unknown"));
    },
    resources: function (n) {
      return ((n.system || {}).cpu || {}).percent_1m_avg || ((n.system || {}).cpu || {}).percent || 0;
    },
    lastSeen: function (n) {
      return n.last_seen || "";
    },
  };
  var sortedMachineNodes = sortRows(
    machineNodes,
    machineSort,
    machineAccessors,
  );
  var runnerAccessors = {
    number: function (r) {
      return parseRunnerName(r.name).number;
    },
    runner: function (r) {
      return r.name;
    },
    state: function (r) {
      return r.busy ? "busy" : r.status;
    },
    labels: function (r) {
      return (r.labels || [])
        .map(function (l) {
          return l.name || l;
        })
        .join(", ");
    },
  };
  var machineCount = machineNodes.length;
  var machineOnline = machineNodes.filter(function (n) {
    return n.online;
  }).length;
  var queued =
    stats.queued != null ? stats.queued : queue.queued_count || 0;
  var running =
    stats.in_progress != null
      ? stats.in_progress
      : queue.in_progress_count || 0;
  var openPrs = stats.org_open_prs != null ? stats.org_open_prs : "-";
  var openIssues =
    stats.org_open_issues != null ? stats.org_open_issues : "-";
  var completedRuns = stats.runs_completed || 0;
  var localDisk = (p.system || {}).disk || {};
  var diskPressure = localDisk.pressure || {};
  var diskStatus = diskPressure.status || "unknown";
  var diskClass =
    diskStatus === "critical"
      ? "storage-critical"
      : diskStatus === "warning"
        ? "storage-warning"
        : "";
  var filteredRunners = runners.filter(function (r) {
    if (filter === "online") return r.status === "online" && !r.busy;
    if (filter === "busy") return r.busy;
    if (filter === "offline") return r.status !== "online";
    return true;
  });
  var visibleIds = {};
  filteredRunners.forEach(function (r) {
    visibleIds[r.id] = true;
  });
  function toggleMachine(name) {
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
  var heroResult = fleetAlerts.computeFleetAlerts({
    machineCount: machineCount,
    machineOnline: machineOnline,
    machineNodes: machineNodes,
    watchdog: watchdog,
    stats: stats,
    completedRuns: completedRuns,
    runnerAudit: runnerAudit,
  });
  var heroAlerts = heroResult.alerts;
  var heroLevel = heroResult.level;
  var heroLevelLabel = fleetAlerts.fleetLevelLabel(heroLevel);
  var heroLevelColor = heroLevel === "ok"
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
          : heroAlerts.slice(0, 3).map(function (alert) {
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
            .filter(function (n) {
              return (
                resolveVisibility(
                  n,
                  (n.health && n.health.runners_registered) || 0,
                ).state !== "full_telemetry"
              );
            })
            .map(function (n) {
              var vis = resolveVisibility(
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
        icon: I.server(16),
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
          sortedMachineNodes.map(function (n) {
            var mrs = runnersByMachine[n.name] || [];
            var onlineRunners = mrs.filter(function (r) {
              return r.status === "online";
            }).length;
            var sys = n.system || {};
            var cpu = sys.cpu || {};
            var mem = sys.memory || {};
            var disk = sys.disk || {};
            var reason =
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
        icon: I.server(16),
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
          I.play(12),
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
          I.stop(12),
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
          I.arrowUp(12),
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
          I.arrowDown(12),
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
          .map(function (r) {
            var parsed = parseRunnerName(r.name);
            var telemetry = machineTelemetryForRunner(r, nodesByName);
            var currentRun = runnerCurrentRun(r, p.runs || []);
            var state = r.busy ? "busy" : r.status;
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
          var machineRunners = (runnersByMachine[machine] || []).filter(
            function (runner) {
              return visibleIds[runner.id];
            },
          );
          if (!machineRunners.length) return null;
          var sortedMachineRunners = sortRows(
            machineRunners,
            runnerTableSort,
            runnerAccessors,
          );
          var node = nodesByName[machine.toLowerCase()] || {};
          var sys = node.system || {};
          var cpu = sys.cpu || {};
          var mem = sys.memory || {};
          var onlineCount = machineRunners.filter(function (r) {
            return r.status === "online";
          }).length;
          var busyCount = machineRunners.filter(function (r) {
            return r.busy;
          }).length;
          var open = expanded[machine] !== false;
          var deploy =
            node.health && node.health.deployment
              ? node.health.deployment.git_sha
              : "";
          var stale =
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
                    sortedMachineRunners.map(function (r) {
                      var parsed = parseRunnerName(r.name);
                      var st = r.busy ? "busy" : r.status;
                      var customLabels = (r.labels || [])
                        .filter(function (l) {
                          var n = l.name || l;
                          return (
                            n !== "self-hosted" &&
                            n !== "Linux" &&
                            n !== "X64" &&
                            !n.startsWith("d-sorg-fleet")
                          );
                        })
                        .map(function (l) {
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

// ════════════════════════ ANALYSIS TAB (orchestrator) ════════════════════════
// Sub-tab leaf panels (StatsTab / PerformanceTab / AnalysisOutcomesTab /
// ReportsTab) are extracted to ../pages/Analysis; the History sub-tab still
// renders the legacy HistoryTab below.
function AnalysisTab(p) {
  var legacyKey = isAnalysisTabKey(p.activeTab) && p.activeTab !== "analysis" ? p.activeTab : null;
  var initial = legacyKey || localStorage.getItem("analysis-subtab") || "outcomes";
  var ss = React.useState(initial);
  var subTab = ss[0], setSubTab = ss[1];
  function changeSubTab(key) {
    setSubTab(key);
    try { localStorage.setItem("analysis-subtab", key); } catch (e) {}
  }
  return h(
    "div",
    null,
    h(SubTabs, {
      tabs: [
        { key: "outcomes", label: "Outcomes" },
        { key: "stats", label: "Durations" },
        { key: "history", label: "History", badge: (p.runs || []).length || null },
        { key: "performance", label: "Performance" },
        { key: "reports", label: "Reports", badge: (p.reports || []).length || null },
      ],
      activeKey: subTab,
      onChange: changeSubTab,
      storageKey: "analysis-subtab",
    }),
    subTab === "outcomes"
      ? h(AnalysisOutcomesTab, null)
      : subTab === "stats"
        ? h(StatsTab, null)
        : subTab === "history"
          ? h(HistoryTab, { runs: p.runs, runners: p.runners })
          : subTab === "performance"
            ? h(PerformanceTab, null)
            : h(ReportsTab, { reports: p.reports, loading: p.reportsLoading }),
  );
}

// ════════════════════════ LOCAL APPS TAB ════════════════════════
function localAppHasUpdateAvailable(a) {
  return a.drift && a.drift.behind > 0 && a.drift.ahead === 0;
}

function localAppUnhealthy(a) {
  return a.health && a.health.available && a.health.ok === false;
}

function localAppNeedsAttention(a) {
  return localAppHasUpdateAvailable(a) || localAppUnhealthy(a);
}

// ════════════════════════ MAIN APP ════════════════════════


function App({ initialTab, onTabChange, activeTab, chromeless }: { initialTab?: string; onTabChange?: (tab: string) => void; activeTab?: string; chromeless?: boolean } = {}) {
  // When `activeTab` is supplied the component is *controlled* by the new
  // desktop shell (#802): the shell owns navigation (sidebar + slim toolstrip)
  // and App just renders the page body for the requested tab. When it is
  // omitted, App is uncontrolled and keeps its own internal tab state plus its
  // legacy top toolstrip — preserving the reversible legacy shell.
  var ts = React.useState(initialTab ?? "overview");
  var internalTab = ts[0],
    _setTabInternal = ts[1];
  var tab = activeTab != null ? activeTab : internalTab;
  var setTab = React.useCallback(function(nextTab: string) {
    _setTabInternal(nextTab);
    if (onTabChange) onTabChange(nextTab);
  }, [onTabChange]);
  // Conductor integration (issue #1282): probe the orchestrator surface once.
  // The tab is only shown when the backend feature flag is enabled (the probe
  // returns non-404). This keeps the surface inert/reversible by default and
  // orthogonal — a probe failure never blocks other tabs.

  // Fleet event feed (issue #863): durable, persisted history of runner/disk
  // events. The event-derived alerts (offline + disk pressure) are folded into
  // the consolidated AlertsCenter pill so the header surfaces "runner(s)
  // offline — disk pressure" without a screen-covering pop-up.
  var fleetEvents = useFleetEvents();

  var conductorState = React.useState(false);
  var conductorEnabled = conductorState[0],
    setConductorEnabled = conductorState[1];
  React.useEffect(function () {
    legacyFetch("/api/orchestrator/queue", { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { setConductorEnabled(r.status !== 404); })
      .catch(function () { setConductorEnabled(false); });
  }, []);
  var rs = React.useState([]);
  var runners = rs[0],
    setRunners = rs[1];
  var ws = React.useState([]);
  var runs = ws[0],
    setRuns = ws[1];
  var er = React.useState([]);
  var enrichedRuns = er[0],
    setEnrichedRuns = er[1];
  var wd = React.useState({});
  var watchdog = wd[0],
    setWatchdog = wd[1];
  var ghs = React.useState({});
  var githubStatus = ghs[0],
    setGithubStatus = ghs[1];
  var ss = React.useState({});
  var system = ss[0],
    setSystem = ss[1];
  var xs = React.useState({});
  var stats = xs[0],
    setStats = xs[1];
  var os = React.useState([]);
  var repos = os[0],
    setRepos = os[1];
  var rl = React.useState(false);
  var reposLoading = rl[0],
    setReposLoading = rl[1];
  var al = React.useState(false);
  var actionLoading = al[0],
    setActionLoading = al[1];
  var cs = React.useState(true);
  var connected = cs[0],
    setConnected = cs[1];
  var ls = React.useState(null);
  var lastUpdate = ls[0],
    setLastUpdate = ls[1];
  var tr = React.useState([]);
  var testRepos = tr[0],
    setTestRepos = tr[1];
  var tl = React.useState(false);
  var testsLoading = tl[0],
    setTestsLoading = tl[1];
  var cr = React.useState([]);
  var ciResults = cr[0],
    setCiResults = cr[1];
  var rp = React.useState([]);
  var reports = rp[0],
    setReports = rp[1];
  var rpl = React.useState(false);
  var reportsLoading = rpl[0],
    setReportsLoading = rpl[1];
  var pr = React.useState(null);
  var principal = pr[0],
    setPrincipal = pr[1];
  var mcv = React.useState(function () {
    return window.matchMedia ? window.matchMedia("(max-width: 768px)").matches : false;
  });
  var mobileCredentialsViewport = mcv[0],
    setMobileCredentialsViewport = mcv[1];
  var qs = React.useState({});
  var queue = qs[0],
    setQueue = qs[1];
  var ql = React.useState(false);
  var queueLoading = ql[0],
    setQueueLoading = ql[1];
  var ms = React.useState({});
  var machinesData = ms[0],
    setMachinesData = ms[1];
  var ml = React.useState(false);
  var machinesLoading = ml[0],
    setMachinesLoading = ml[1];
  var rfs = React.useState({ error: null, stale: false, lastSuccessful: null });
  var runnerFetchState = rfs[0],
    setRunnerFetchState = rfs[1];
  var mfs = React.useState({ error: null, stale: false, lastSuccessful: null });
  var machineFetchState = mfs[0],
    setMachineFetchState = mfs[1];
  var sjs = React.useState({});
  var scheduledJobs = sjs[0],
    setScheduledJobs = sjs[1];
  // Loading flag retained only as a setter: the legacy toolstrip badge still
  // reads `scheduledJobs` (the count) but no longer renders a loading state —
  // the Schedules tab body and its loading UI now live in pages/ScheduledJobs.
  var sjl = React.useState(false);
  var setScheduledJobsLoading = sjl[1];
  var las = React.useState({});
  var localApps = las[0],
    setLocalApps = las[1];
  var lal = React.useState(false);
  var localAppsLoading = lal[0],
    setLocalAppsLoading = lal[1];
  var rcs = React.useState(null);
  var runnerCapacity = rcs[0],
    setRunnerCapacity = rcs[1];
  var rcl = React.useState(false);
  var runnerCapacityLoading = rcl[0],
    setRunnerCapacityLoading = rcl[1];
  var ds = React.useState({});
  var deployment = ds[0],
    setDeployment = ds[1];
  var dss = React.useState({});
  var deploymentState = dss[0],
    setDeploymentState = dss[1];
  var dsl = React.useState(false);
  var deploymentStateLoading = dsl[0],
    setDeploymentStateLoading = dsl[1];
  var arcs = React.useState({});
  var remediationConfig = arcs[0],
    setRemediationConfig = arcs[1];
  var arws = React.useState({});
  var remediationWorkflows = arws[0],
    setRemediationWorkflows = arws[1];
  var arl = React.useState(false);
  var remediationLoading = arl[0],
    setRemediationLoading = arl[1];
  var are = React.useState(null);
  var remediationError = are[0],
    setRemediationError = are[1];
  var arp = React.useState("jules_api");
  var remediationProvider = arp[0],
    setRemediationProvider = arp[1];
  var arm = React.useState("");
  var remediationModel = arm[0],
    setRemediationModel = arm[1];
  var arps = React.useState(null);
  var remediationPlan = arps[0],
    setRemediationPlan = arps[1];
  var ards = React.useState(null);
  var remediationDispatchState = ards[0],
    setRemediationDispatchState = ards[1];
  var arrs = React.useState("");
  var remediationSelectedRunId = arrs[0],
    setRemediationSelectedRunId = arrs[1];
  var rhs = React.useState([]);
  var remediationHistory = rhs[0],
    setRemediationHistory = rhs[1];
  var wts = React.useState([]);
  var workflowsList = wts[0],
    setWorkflowsList = wts[1];
  var wtl = React.useState(false);
  var workflowsListLoading = wtl[0],
    setWorkflowsListLoading = wtl[1];
  var wte = React.useState(null);
  var workflowsListError = wte[0],
    setWorkflowsListError = wte[1];
  var crs = React.useState({ probes: [], summary: {} });
  var credentialsData = crs[0],
    setCredentialsData = crs[1];
  var crl = React.useState(false);
  var credentialsLoading = crl[0],
    setCredentialsLoading = crl[1];
  var cre = React.useState(null);
  var credentialsError = cre[0],
    setCredentialsError = cre[1];
  var fos = React.useState({});
  var fleetOrchData = fos[0],
    setFleetOrchData = fos[1];
  var fol = React.useState(false);
  var fleetOrchLoading = fol[0],
    setFleetOrchLoading = fol[1];
  var foe = React.useState(null);
  var fleetOrchError = foe[0],
    setFleetOrchError = foe[1];

  var acs = React.useState([]);
  var assessmentScores = acs[0],
    setAssessmentScores = acs[1];
  var acl = React.useState(false);
  var assessmentLoading = acl[0],
    setAssessmentLoading = acl[1];
  var ace = React.useState(null);
  var assessmentError = ace[0],
    setAssessmentError = ace[1];
  var frs2 = React.useState([]);
  var featureRequests = frs2[0],
    setFeatureRequests = frs2[1];
  var frt = React.useState([]);
  var promptTemplates = frt[0],
    setPromptTemplates = frt[1];
  var frstds = React.useState({});
  var featureStandards = frstds[0],
    setFeatureStandards = frstds[1];
  var frl = React.useState(false);
  var featureRequestsLoading = frl[0],
    setFeatureRequestsLoading = frl[1];
  var pns = React.useState({ notes: "", enabled: true });
  var promptNotes = pns[0],
    setPromptNotes = pns[1];
  var asstS = React.useState(lsGet(ASST_LS.open, lsGet(ASST_LS.openByDefault, false)));
  var asstOpen = asstS[0], setAsstOpen = asstS[1];
  function toggleAsst() { setAsstOpen(function (o) { var n = !o; lsSet(ASST_LS.open, n); return n; }); }
  var rmS = React.useState(null);
  var recoveryModal = rmS[0], setRecoveryModal = rmS[1];
  var seS = React.useState(false);
  var sessionExpiredOpen = seS[0], setSessionExpiredOpen = seS[1];
  var rauS = React.useState({ violations: [], last_checked: null, error: null });
  var runnerAudit = rauS[0], setRunnerAudit = rauS[1];
  // The standalone billing/audit dismiss state was removed in issue #819 — the
  // alert now lives in the consolidated AlertsCenter with durable ack/snooze.

  React.useEffect(function () {
    var unsubscribe = subscribeSessionExpired(function () {
      setSessionExpiredOpen(true);
    });
    window._showAuthError = function () {
      setSessionExpiredOpen(true);
    };
    return function () {
      unsubscribe();
      if (window._showAuthError) delete window._showAuthError;
    };
  }, []);

  React.useEffect(function () {
    return installWheelValueGuard(document);
  }, []);

  React.useEffect(function () {
    if (!window.matchMedia) return;
    var media = window.matchMedia("(max-width: 768px)");
    function onMobileCredentialViewportChange(event) {
      setMobileCredentialsViewport(event.matches);
    }
    setMobileCredentialsViewport(media.matches);
    if (media.addEventListener) {
      media.addEventListener("change", onMobileCredentialViewportChange);
      return function () {
        media.removeEventListener("change", onMobileCredentialViewportChange);
      };
    }
    media.addListener(onMobileCredentialViewportChange);
    return function () {
      media.removeListener(onMobileCredentialViewportChange);
    };
  }, []);

  function fetchFleetOrchestration() {
    setFleetOrchLoading(true);
    legacyFetch("/api/fleet/orchestration")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setFleetOrchData(d || {});
        setFleetOrchError(null);
        setFleetOrchLoading(false);
      })
      .catch(function () {
        setFleetOrchError("Failed to load fleet orchestration data.");
        setFleetOrchLoading(false);
      });
  }

  function orchDispatch(params) {
    return legacyFetch("/api/fleet/orchestration/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Dispatch failed");
        return d;
      });
    });
  }

  function orchDeploy(params) {
    return legacyFetch("/api/fleet/orchestration/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Deploy failed");
        return d;
      });
    });
  }

  function fetchCredentials() {
    setCredentialsLoading(true);
    legacyFetch("/api/credentials")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setCredentialsData(d || {});
        setCredentialsError(null);
        setCredentialsLoading(false);
      })
        .catch(function () {
          setCredentialsError("Failed to probe credentials.");
          setCredentialsLoading(false);
        });
  }

  function setCredentialKey(probe, keyValue) {
    var provider = probe && probe.key_provider;
    if (!provider) return;
    var providerLabel = probe.label || probe.name || provider;
    if (!keyValue) return;
    if (!keyValue) return;
    legacyFetch("/api/credentials/set-key", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({
        provider: provider,
        key: keyValue,
        restart_maxwell: false,
      }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error((data && data.detail) || ("HTTP " + r.status));
          return data;
        });
      })
      .then(function () {
        fetchCredentials();
      })
      .catch(function (err) {
        setCredentialsError(err.message || "Failed to save key.");
      });
  }

  function fetchWorkflowsList() {
    setWorkflowsListLoading(true);
    legacyFetch("/api/workflows/list")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setWorkflowsList((d && d.workflows) || []);
        setWorkflowsListError(null);
        setWorkflowsListLoading(false);
      })
      .catch(function () {
        setWorkflowsListError("Failed to load workflows list.");
        setWorkflowsListLoading(false);
      });
  }
  function dispatchWorkflow(params) {
    return legacyFetch("/api/workflows/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Dispatch failed");
        return d;
      });
    });
  }
  var mxs = React.useState({});
  var maxwellStatus = mxs[0],
    setMaxwellStatus = mxs[1];
  var mxl = React.useState(false);
  var maxwellLoading = mxl[0],
    setMaxwellLoading = mxl[1];
  var mxe = React.useState(null);
  var maxwellError = mxe[0],
    setMaxwellError = mxe[1];

  function fetchMaxwellStatus() {
    setMaxwellLoading(true);
    legacyFetch("/api/maxwell/status")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setMaxwellStatus(d || {});
        setMaxwellError(null);
        setMaxwellLoading(false);
      })
      .catch(function () {
        setMaxwellError("Failed to probe Maxwell status.");
        setMaxwellLoading(false);
      });
  }
  function maxwellControl(params) {
    return legacyFetch("/api/maxwell/control", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Control failed");
        return d;
      });
    });
  }

  function fetchOptionalStats() {
    legacyFetch("/api/stats")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setStats(d);
      })
      .catch(function () {});
  }
  function fetchFleet() {
    fetchOptionalStats();
    Promise.all([
      legacyFetch("/api/runners")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/runs?per_page=30")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/system")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/fleet/schedule")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/github/status")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
    ])
      .then(function (r) {
        if (r[0] && r[0].runners) {
          setRunners(r[0].runners);
          setRunnerFetchState({
            error: r[0].error || null,
            stale: !!(r[0].stale || r[0].degraded),
            lastSuccessful: new Date().toISOString(),
          });
          setConnected(!r[0].error);
        } else {
          setRunnerFetchState({
            error: "Runner status unavailable; retaining last known data.",
            stale: runners.length > 0,
            lastSuccessful: runnerFetchState.lastSuccessful,
          });
          setConnected(false);
        }
        if (r[1] && r[1].workflow_runs) {
          setRuns(r[1].workflow_runs);
        }
        if (r[2] && r[2].hostname) {
          setSystem(r[2]);
        }
        if (r[3]) {
          setRunnerCapacity(r[3]);
        }
        if (r[4]) {
          setGithubStatus(r[4]);
        }
        setLastUpdate(new Date().toLocaleTimeString());
      })
      .catch(function () {
        setConnected(false);
      });
  }
  function fetchRepos() {
    setReposLoading(true);
    legacyFetch("/api/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setRepos(d.repos);
        setReposLoading(false);
      })
      .catch(function () {
        setReposLoading(false);
      });
  }
  function fetchTests() {
    setTestsLoading(true);
    legacyFetch("/api/heavy-tests/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setTestRepos(d.repos);
        setTestsLoading(false);
      })
      .catch(function () {
        setTestsLoading(false);
      });
  }
  function fetchCiResults() {
    legacyFetch("/api/tests/ci-results")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.results) setCiResults(d.results);
      })
      .catch(function () {});
  }
  function fetchReports() {
    setReportsLoading(true);
    legacyFetch("/api/reports")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.reports) setReports(d.reports);
        setReportsLoading(false);
      })
      .catch(function () {
        setReportsLoading(false);
      });
  }
  function fetchQueue() {
    setQueueLoading(true);
    legacyFetch("/api/queue")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setQueue(d);
        setQueueLoading(false);
      })
      .catch(function () {
        setQueueLoading(false);
      });
  }
  function fetchMachines() {
    setMachinesLoading(true);
    legacyFetch("/api/fleet/nodes")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) {
          setMachinesData(d);
          setMachineFetchState({
            error: d.fleet_probe_error || null,
            stale: !!(d.partial || d.degraded),
            lastSuccessful: new Date().toISOString(),
          });
        } else {
          setMachineFetchState({
            error: "Machine health unavailable; retaining last known data.",
            stale: (machinesData.nodes || []).length > 0,
            lastSuccessful: machineFetchState.lastSuccessful,
          });
        }
        setMachinesLoading(false);
      })
      .catch(function () {
        setMachineFetchState({
          error: "Machine health unavailable; retaining last known data.",
          stale: (machinesData.nodes || []).length > 0,
          lastSuccessful: machineFetchState.lastSuccessful,
        });
        setMachinesLoading(false);
      });
  }
  function fetchLocalApps() {
    setLocalAppsLoading(true);
    legacyFetch("/api/local-apps")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setLocalApps(d);
        setLocalAppsLoading(false);
      })
      .catch(function () {
        setLocalAppsLoading(false);
      });
  }
  function fetchEnrichedRuns() {
    legacyFetch("/api/runs/enriched?per_page=50")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.workflow_runs) setEnrichedRuns(d.workflow_runs);
      })
      .catch(function () {});
  }
  function fetchWatchdog() {
    legacyFetch("/api/watchdog")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setWatchdog(d);
      })
      .catch(function () {});
  }
  function fetchDeployment() {
    legacyFetch("/api/deployment")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeployment(d);
      })
      .catch(function () {});
  }
  function fetchDeploymentState() {
    setDeploymentStateLoading(true);
    legacyFetch("/api/deployment/state")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeploymentState(d);
        setDeploymentStateLoading(false);
      })
      .catch(function () {
        setDeploymentStateLoading(false);
      });
  }
  function fetchRemediationConfig() {
    setRemediationLoading(true);
    Promise.all([
      legacyFetch("/api/agent-remediation/config").then(function (r) {
        return r.json();
      }),
      legacyFetch("/api/agent-remediation/workflows").then(function (r) {
        return r.json();
      }),
      legacyFetch("/api/agent-remediation/history")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return { history: [] };
        }),
    ])
      .then(function (data) {
        setRemediationConfig(data[0] || {});
        setRemediationWorkflows(data[1] || {});
        setRemediationHistory((data[2] && data[2].history) || []);
        setRemediationProvider(
          (data[0] &&
            data[0].policy &&
            data[0].policy.default_provider) ||
            "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function () {
        setRemediationError(
          "Failed to load remediation controls from the dashboard backend.",
        );
        setRemediationLoading(false);
      });
  }
  function saveRemediationConfig(policy) {
    setRemediationLoading(true);
    return legacyFetch("/api/agent-remediation/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ policy: policy }),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Save failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationConfig(d || {});
        setRemediationProvider(
          (d && d.policy && d.policy.default_provider) || "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
        return d;
      })
      .catch(function (e) {
        setRemediationError(e.message || "Save failed");
        setRemediationLoading(false);
        throw e;
      });
  }
  function buildRemediationContext(run) {
    if (!run) return null;
    var branch = run.head_branch || "";
    var repoName =
      run.repository && run.repository.name ? run.repository.name : "";
    var workflowName = run.name || run.workflow_name || "CI Standard";
    return {
      repository: repoName,
      workflow_name: workflowName,
      branch: branch,
      run_id: run.id,
      failure_reason:
        workflowName + " failed for " + repoName + " on " + branch,
      protected_branch: branch === "main" || branch === "master",
      attempts: [],
    };
  }
  function previewRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before previewing remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState(null);
    legacyFetch("/api/agent-remediation/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider_override: remediationProvider,
          model_override: remediationModel || undefined,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Preview failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationPlan(d);
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function (e) {
        setRemediationPlan(null);
        setRemediationError(e.message || "Preview failed");
        setRemediationLoading(false);
      });
  }
  function dispatchRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before dispatching remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState({
      note:
        "Dispatch submitted for " +
        payload.repository +
        " #" +
        payload.run_id +
        ". Waiting for agent heartbeat.",
    });
    legacyFetch("/api/agent-remediation/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider: remediationProvider,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Dispatch failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationDispatchState({
          note:
            "Dispatched " + d.provider + " through " + d.workflow + ".",
        });
        setRemediationError(null);
        setRemediationLoading(false);
        // Refresh history after dispatch
        legacyFetch("/api/agent-remediation/history")
          .then(function (r) {
            return r.json();
          })
          .then(function (hd) {
            if (hd && hd.history) setRemediationHistory(hd.history);
          })
          .catch(function () {});
      })
      .catch(function (e) {
        setRemediationDispatchState({
          error: e.message || "Dispatch failed",
        });
        setRemediationError(e.message || "Dispatch failed");
        setRemediationLoading(false);
      });
  }
  function fetchScheduledJobs() {
    setScheduledJobsLoading(true);
    legacyFetch("/api/scheduled-workflows")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setScheduledJobs(d);
        setScheduledJobsLoading(false);
      })
      .catch(function () {
        setScheduledJobsLoading(false);
      });
  }
  function fetchRunnerCapacity() {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }
  function saveRunnerCapacity(schedule, apply) {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ schedule: schedule, apply: apply }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("save failed");
        return r.json();
      })
      .then(function (d) {
        setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
        setTimeout(fetchFleet, 2000);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }


  function fetchAssessments() {
    setAssessmentLoading(true);
    setAssessmentError(null);
    legacyFetch("/api/assessments/scores")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setAssessmentScores(d.scores || []);
        setAssessmentLoading(false);
      })
      .catch(function () {
        setAssessmentError("Failed to load assessment scores");
        setAssessmentLoading(false);
      });
  }

  function dispatchAssessment(params) {
    return legacyFetch("/api/assessments/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      if (!r.ok)
        return r.json().then(function (e) {
          throw new Error(e.detail || "dispatch failed");
        });
      return r.json();
    });
  }

  function fetchFeatureRequests() {
    setFeatureRequestsLoading(true);
    Promise.all([
      legacyFetch("/api/feature-requests")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return { requests: [] };
        }),
      legacyFetch("/api/feature-requests/templates")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return { templates: [], promptNotes: { notes: "", enabled: true } };
        }),
    ]).then(function (results) {
      setFeatureRequests(results[0].requests || []);
      setPromptTemplates(results[1].templates || []);
      if (results[1].promptNotes) {
        setPromptNotes(results[1].promptNotes);
      }
      setFeatureRequestsLoading(false);
    });
  }

  function dispatchFeatureRequest(params) {
    return legacyFetch("/api/feature-requests/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      if (!r.ok)
        return r.json().then(function (e) {
          throw new Error(e.detail || "dispatch failed");
        });
      return r.json();
    });
  }

  function savePromptTemplate(params) {
    return legacyFetch("/api/prompt-templates", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    })
      .then(function (r) {
        if (!r.ok)
          return r.json().then(function (e) {
            throw new Error(e.detail || "save failed");
          });
        return r.json();
      })
      .then(function (d) {
        fetchFeatureRequests();
        return d;
      });
  }

  function updatePromptNotes(params) {
    return legacyFetch("/api/settings/prompt-notes", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }).then(function (r) {
      if (!r.ok)
        return r.json().then(function (e) {
          throw new Error(e.detail || "save failed");
        });
      return r.json();
    });
  }

  function fetchOptionalStats() {
    legacyFetch("/api/stats")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setStats(d);
      })
      .catch(function () {});
  }
  function fetchFleet() {
    fetchOptionalStats();
    Promise.all([
      legacyFetch("/api/runners")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/runs?per_page=30")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/system")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/fleet/schedule")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
    ])
      .then(function (r) {
        if (r[0] && r[0].runners) {
          setRunners(r[0].runners);
          setRunnerFetchState({
            error: r[0].error || null,
            stale: !!(r[0].stale || r[0].degraded),
            lastSuccessful: new Date().toISOString(),
          });
          setConnected(!r[0].error);
        } else {
          setRunnerFetchState({
            error: "Runner status unavailable; retaining last known data.",
            stale: runners.length > 0,
            lastSuccessful: runnerFetchState.lastSuccessful,
          });
          setConnected(false);
        }
        if (r[1] && r[1].workflow_runs) {
          setRuns(r[1].workflow_runs);
        }
        if (r[2] && r[2].hostname) {
          setSystem(r[2]);
        }
        if (r[3]) {
          setRunnerCapacity(r[3]);
        }
        setLastUpdate(new Date().toLocaleTimeString());
      })
      .catch(function () {
        setConnected(false);
      });
  }
  function fetchRepos() {
    setReposLoading(true);
    legacyFetch("/api/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setRepos(d.repos);
        setReposLoading(false);
      })
      .catch(function () {
        setReposLoading(false);
      });
  }
  function fetchTests() {
    setTestsLoading(true);
    legacyFetch("/api/heavy-tests/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setTestRepos(d.repos);
        setTestsLoading(false);
      })
      .catch(function () {
        setTestsLoading(false);
      });
  }
  function fetchCiResults() {
    legacyFetch("/api/tests/ci-results")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.results) setCiResults(d.results);
      })
      .catch(function () {});
  }
  function fetchReports() {
    setReportsLoading(true);
    legacyFetch("/api/reports")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.reports) setReports(d.reports);
        setReportsLoading(false);
      })
      .catch(function () {
        setReportsLoading(false);
      });
  }
  function fetchQueue() {
    setQueueLoading(true);
    legacyFetch("/api/queue")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setQueue(d);
        setQueueLoading(false);
      })
      .catch(function () {
        setQueueLoading(false);
      });
  }
  function fetchMachines() {
    setMachinesLoading(true);
    legacyFetch("/api/fleet/nodes")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) {
          setMachinesData(d);
          setMachineFetchState({
            error: d.fleet_probe_error || null,
            stale: !!(d.partial || d.degraded),
            lastSuccessful: new Date().toISOString(),
          });
        } else {
          setMachineFetchState({
            error: "Machine health unavailable; retaining last known data.",
            stale: (machinesData.nodes || []).length > 0,
            lastSuccessful: machineFetchState.lastSuccessful,
          });
        }
        setMachinesLoading(false);
      })
      .catch(function () {
        setMachineFetchState({
          error: "Machine health unavailable; retaining last known data.",
          stale: (machinesData.nodes || []).length > 0,
          lastSuccessful: machineFetchState.lastSuccessful,
        });
        setMachinesLoading(false);
      });
  }
  function fetchLocalApps() {
    setLocalAppsLoading(true);
    legacyFetch("/api/local-apps")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setLocalApps(d);
        setLocalAppsLoading(false);
      })
      .catch(function () {
        setLocalAppsLoading(false);
      });
  }
  function fetchEnrichedRuns() {
    legacyFetch("/api/runs/enriched?per_page=50")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.workflow_runs) setEnrichedRuns(d.workflow_runs);
      })
      .catch(function () {});
  }
  function fetchWatchdog() {
    legacyFetch("/api/watchdog")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setWatchdog(d);
      })
      .catch(function () {});
  }
  function fetchDeployment() {
    legacyFetch("/api/deployment")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeployment(d);
      })
      .catch(function () {});
  }
  function fetchDeploymentState() {
    setDeploymentStateLoading(true);
    legacyFetch("/api/deployment/state")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeploymentState(d);
        setDeploymentStateLoading(false);
      })
      .catch(function () {
        setDeploymentStateLoading(false);
      });
  }
  function fetchRemediationConfig() {
    setRemediationLoading(true);
    Promise.all([
      legacyFetch("/api/agent-remediation/config").then(function (r) {
        return r.json();
      }),
      legacyFetch("/api/agent-remediation/workflows").then(function (r) {
        return r.json();
      }),
    ])
      .then(function (data) {
        setRemediationConfig(data[0] || {});
        setRemediationWorkflows(data[1] || {});
        setRemediationProvider(
          (data[0] &&
            data[0].policy &&
            data[0].policy.default_provider) ||
            "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function () {
        setRemediationError(
          "Failed to load remediation controls from the dashboard backend.",
        );
        setRemediationLoading(false);
      });
  }
  function saveRemediationConfig(policy) {
    setRemediationLoading(true);
    return legacyFetch("/api/agent-remediation/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ policy: policy }),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Save failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationConfig(d || {});
        setRemediationProvider(
          (d && d.policy && d.policy.default_provider) || "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
        return d;
      })
      .catch(function (e) {
        setRemediationError(e.message || "Save failed");
        setRemediationLoading(false);
        throw e;
      });
  }
  function buildRemediationContext(run) {
    if (!run) return null;
    var branch = run.head_branch || "";
    var repoName = run.repository && run.repository.name
      ? run.repository.name
      : "";
    var workflowName = run.name || run.workflow_name || "CI Standard";
    return {
      repository: repoName,
      workflow_name: workflowName,
      branch: branch,
      run_id: run.id,
      failure_reason:
        workflowName +
        " failed for " +
        repoName +
        " on " +
        branch,
      protected_branch: branch === "main" || branch === "master",
      attempts: [],
    };
  }
  function previewRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before previewing remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState(null);
    legacyFetch("/api/agent-remediation/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider_override: remediationProvider,
          model_override: remediationModel || undefined,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Preview failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationPlan(d);
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function (e) {
        setRemediationPlan(null);
        setRemediationError(e.message || "Preview failed");
        setRemediationLoading(false);
      });
  }
  function dispatchRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before dispatching remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState({
      note:
        "Dispatch submitted for " +
        payload.repository +
        " #" +
        payload.run_id +
        ". Waiting for agent heartbeat.",
    });
    legacyFetch("/api/agent-remediation/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider: remediationProvider,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Dispatch failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationDispatchState({
          note:
            "Dispatched " + d.provider + " through " + d.workflow + ".",
        });
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function (e) {
        setRemediationDispatchState({
          error: e.message || "Dispatch failed",
        });
        setRemediationError(e.message || "Dispatch failed");
        setRemediationLoading(false);
      });
  }
  function fetchScheduledJobs() {
    setScheduledJobsLoading(true);
    legacyFetch("/api/scheduled-workflows")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setScheduledJobs(d);
        setScheduledJobsLoading(false);
      })
      .catch(function () {
        setScheduledJobsLoading(false);
      });
  }
  function fetchRunnerCapacity() {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }
  function saveRunnerCapacity(schedule, apply) {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ schedule: schedule, apply: apply }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("save failed");
        return r.json();
      })
      .then(function (d) {
        setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
        setTimeout(fetchFleet, 2000);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }

  function fetchPrincipal() {
    legacyFetch("/api/auth/me")
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) { setPrincipal(d); })
      .catch(function() { setPrincipal(null); });
  }

  function fetchRunnerAudit() {
    legacyFetch("/api/runner-routing-audit")
      .then(function(r) { return r.json(); })
      .then(function(d) {
        // NOTE (issue #819): we intentionally do NOT reset any dismissal here.
        // The old `setAuditBannerDismissed(false)` ran on every poll and
        // structurally undid the operator's Dismiss. Acknowledgement is now
        // durable via lib/alertAck (keyed by alert id + contentHash), so a
        // re-poll never re-surfaces an acknowledged alert unless its content
        // materially changes.
        setRunnerAudit(d || { violations: [], last_checked: null, error: null });
      })
      .catch(function() {});
  }

  function triggerRunnerAuditRefresh() {
    legacyFetch("/api/runner-routing-audit/refresh", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function() { setTimeout(fetchRunnerAudit, 3000); })
      .catch(function() {});
  }

  React.useEffect(function () {
    fetchPrincipal();
    fetchFleet();
    fetchRepos();
    fetchTests();
    fetchCiResults();
    fetchReports();
    fetchQueue();
    fetchMachines();
    fetchLocalApps();
    fetchEnrichedRuns();
    fetchWatchdog();
    fetchDeployment();
    fetchDeploymentState();
    fetchScheduledJobs();
    fetchRunnerCapacity();
    fetchRunnerAudit();
    var t1 = setInterval(fetchFleet, 30000);
    var t2 = setInterval(fetchRepos, 120000);
    var t3 = setInterval(fetchTests, 120000);
    var t3b = setInterval(fetchCiResults, 120000);
    var t4 = setInterval(fetchReports, 300000);
    var t5 = setInterval(fetchQueue, 60000);
    var t6 = setInterval(fetchMachines, 60000);
    var t7 = setInterval(fetchEnrichedRuns, 60000);
    var t8 = setInterval(fetchWatchdog, 120000);
    var t9 = setInterval(fetchScheduledJobs, 300000);
    var t10 = setInterval(fetchLocalApps, 90000);
    var t11 = setInterval(fetchRunnerCapacity, 60000);
    var t12 = setInterval(fetchDeployment, 300000);
    var t13 = setInterval(fetchDeploymentState, 300000);
    var t14 = setInterval(fetchRunnerAudit, 300000);
    return function () {
      clearInterval(t1);
      clearInterval(t2);
      clearInterval(t3);
      clearInterval(t3b);
      clearInterval(t4);
      clearInterval(t5);
      clearInterval(t6);
      clearInterval(t7);
      clearInterval(t8);
      clearInterval(t9);
      clearInterval(t10);
      clearInterval(t11);
      clearInterval(t12);
      clearInterval(t13);
      clearInterval(t14);
    };
  }, []);

  React.useEffect(function () {
    var failureCount = 0;
    var maxFailures = 3;
    function checkHealth() {
      legacyFetch("/health", { method: "GET" })
        .then(function (r) {
          if (r.ok) {
            failureCount = 0;
            setRecoveryModal(null);
          } else {
            failureCount++;
            if (failureCount >= maxFailures) {
              setRecoveryModal({ visible: true });
            }
          }
        })
        .catch(function () {
          failureCount++;
          if (failureCount >= maxFailures) {
            setRecoveryModal({ visible: true });
          }
        });
    }
    var healthInterval = setInterval(checkHealth, 2000);
    return function () { clearInterval(healthInterval); };
  }, []);

  function onFleet(a) {
    setActionLoading(true);
    legacyFetch("/api/fleet/control/" + a, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function () {
        setTimeout(fetchFleet, 2000);
      })
      .finally(function () {
        setTimeout(function () {
          setActionLoading(false);
        }, 2500);
      });
  }
  function onRunner(id, a) {
    setActionLoading(true);
    legacyFetch("/api/runners/" + id + "/" + a, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function () {
        setTimeout(fetchFleet, 2000);
      })
      .finally(function () {
        setTimeout(function () {
          setActionLoading(false);
        }, 2500);
      });
  }

  var asstPosition = lsGet(ASST_LS.position, "right");

  // ─── Consolidated alert surface (issue #819) ────────────────────────────
  // Roll the cross-cutting fleet signals into ONE durable, acknowledgeable
  // surface (status pill + drawer) instead of the old hero list + three sticky
  // banners that re-popped on every poll. Uses the same pure rollup as the
  // FleetTab hero so the two stay in sync (DRY). Telemetry-degraded and GitHub
  // API conditions are folded in as additional synthetic alerts so a single
  // surface owns every cross-cutting signal.
  var alertNodes = (machinesData.nodes || []);
  var alertMachineCount = alertNodes.length;
  var alertMachineOnline = alertNodes.filter(function (n) { return n.online; }).length;
  var appAlertsResult = fleetAlerts.computeFleetAlerts({
    machineCount: alertMachineCount,
    machineOnline: alertMachineOnline,
    machineNodes: alertNodes,
    watchdog: watchdog,
    stats: stats,
    completedRuns: stats.runs_completed || 0,
    runnerAudit: runnerAudit,
  });
  var appAlerts = appAlertsResult.alerts.slice();
  // Fold the former "telemetry degraded" sticky banner into the surface.
  if (runnerFetchState.error || runnerFetchState.stale || machineFetchState.error || machineFetchState.stale) {
    var degradedDetail = [
      runnerFetchState.error
        ? "Runner status is unavailable; showing last known or degraded data."
        : runnerFetchState.stale ? "Runner status is stale." : null,
      machineFetchState.error
        ? "Machine health is partially unavailable; local data is kept when available."
        : machineFetchState.stale ? "Machine health is partial." : null,
    ].filter(Boolean).join(" ");
    appAlerts.push({
      id: "telemetry-degraded",
      level: "warning",
      title: "Fleet telemetry degraded",
      detail: degradedDetail || "Some fleet telemetry is unavailable.",
      contentHash: fleetAlerts.alertContentHash({
        id: "telemetry-degraded", level: "warning",
        title: "Fleet telemetry degraded", detail: degradedDetail,
      }),
    });
  }
  // Fold the former "GitHub API degraded" sticky banner into the surface.
  if (githubStatus && (githubStatus.status === "rate_limited" || githubStatus.status === "auth_error")) {
    var ghDetail = githubStatus.status === "rate_limited"
      ? ("Rate limited" + (githubStatus.retry_after_seconds ? " for about " + githubStatus.retry_after_seconds + "s" : "") + ". Cached local runner data may still be shown.")
      : "Authentication failed. Refresh the dashboard GitHub token before relying on GitHub-backed views.";
    appAlerts.push({
      id: "github-api",
      level: githubStatus.status === "auth_error" ? "critical" : "warning",
      title: "GitHub API degraded",
      detail: ghDetail,
      contentHash: fleetAlerts.alertContentHash({
        id: "github-api",
        level: githubStatus.status === "auth_error" ? "critical" : "warning",
        title: "GitHub API degraded", detail: ghDetail,
      }),
    });
  }
  // Fold the event-derived alerts (disk-pressure, runners-offline) into the
  // consolidated surface (issue #863). De-dup by id so a rollup machines-offline
  // and an event runners-offline don't both show; the event alert carries the
  // richer "disk pressure" reason, so it wins where ids differ.
  (fleetEvents.alerts || []).forEach(function (ea) {
    if (!appAlerts.some(function (a) { return a.id === ea.id; })) {
      appAlerts.push(ea);
    }
  });
  function onAlertNavigate(alertId) {
    if (alertId === "hosted-runners") { setTab("runner-audit"); return; }
    if (alertId === "github-api") { setTab("runner-audit"); return; }
    if (alertId === "machines-offline" || alertId === "telemetry-degraded") { setTab("machines"); return; }
    if (alertId === "disk-pressure" || alertId === "runners-offline") { setTab("events"); return; }
    setTab("overview");
  }

  return h(
    "div",
    null,
    // In chromeless mode the new desktop shell (#802) supplies the navigation
    // chrome (sidebar + slim toolstrip), so App suppresses its own legacy
    // header/toolstrip and renders only the page body below.
    chromeless ? null : h(
      "header",
      { className: "app-header app-header--rows" },
      h(
        "div",
        { className: "app-header__row app-header__row--primary" },
      h(
        "div",
        { className: "app-logo" },
        h(
          "svg",
          { width: 28, height: 28, viewBox: "0 0 32 32" },
          h(
            "defs",
            null,
            h(
              "linearGradient",
              { id: "lg", x1: 0, y1: 0, x2: 1, y2: 1 },
              h("stop", { offset: "0%", stopColor: "var(--accent-blue)" }),
              h("stop", { offset: "100%", stopColor: "var(--accent-purple)" }),
            ),
          ),
          h("rect", { width: 32, height: 32, rx: 6, fill: "url(#lg)" }),
          h("path", { d: "M18 6l-7 13h6l-1 7 7-13h-6z", fill: "white" }),
        ),
        h("span", { className: "logo-text" }, "Dashboard"),
      ),
      h(
        "div",
        { className: "tab-bar", role: "tablist", "aria-label": "Dashboard sections" },
        h(
          "button",
          {
            className: "tab-btn" + (tab === "overview" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "overview",
            onClick: function () {
              setTab("overview");
            },
          },
          I.server(14),
          "Overview",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "remediation" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "remediation",
            onClick: function () {
              setTab("remediation");
              fetchRemediationConfig();
            },
          },
          I.issue(14),
          "Remediation",
          runs.filter(function (run) {
            return run.conclusion === "failure";
          }).length > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(248,81,73,0.15)",
                    color: "var(--accent-red)",
                    marginLeft: 2,
                  },
                },
                runs.filter(function (run) {
                  return run.conclusion === "failure";
                }).length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "agent-dispatch" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "agent-dispatch",
            onClick: function () {
              setTab("agent-dispatch");
            },
          },
          I.issue(14),
          "Dispatch",
          runs.filter(function (run) {
            return run.conclusion === "failure";
          }).length > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(248,81,73,0.15)",
                    color: "var(--accent-red)",
                    marginLeft: 2,
                  },
                },
                runs.filter(function (run) {
                  return run.conclusion === "failure";
                }).length,
              )
            : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "queue" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "queue",
            onClick: function () {
              setTab("queue");
              fetchQueue();
            },
          },
          I.queue(14),
          "Queue",
          (queue.total || 0) > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(88,166,255,0.2)",
                    color: "var(--accent-blue)",
                    marginLeft: 2,
                  },
                },
                queue.total || 0,
              )
            : null,
        ),
        conductorEnabled
          ? h(
              "button",
              {
                className: "tab-btn" + (tab === "conductor" ? " active" : ""),
                role: "tab",
                "aria-selected": tab === "conductor",
                onClick: function () {
                  setTab("conductor");
                },
              },
              I.queue(14),
              "Conductor",
            )
          : null,
        h(
          "button",
          {
            className: "tab-btn" + (tab === "machines" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "machines",
            onClick: function () {
              setTab("machines");
              fetchMachines();
            },
          },
          I.server(14),
          "Machines",
          (machinesData.count || 0) > 1
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(63,185,80,0.15)",
                    color: "var(--accent-green)",
                    marginLeft: 2,
                  },
                },
                machinesData.count || 0,
              )
            : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "org" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "org",
            onClick: function () {
              setTab("org");
            },
          },
          I.repo(14),
          "Organization",
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "tests" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "tests",
            onClick: function () {
              setTab("tests");
            },
          },
          I.flask(14),
          "Tests",
        ),
        h(
          "button",
          {
            className: "tab-btn" + (isAnalysisTabKey(tab) ? " active" : ""),
            role: "tab",
            "aria-selected": isAnalysisTabKey(tab),
            onClick: function () {
              setTab("analysis");
              fetchEnrichedRuns();
              fetchReports();
            },
          },
          I.activity(14),
          "Analysis",
          enrichedRuns.length > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(136,108,228,0.15)",
                    color: "var(--accent-purple)",
                    marginLeft: 2,
                  },
                },
                enrichedRuns.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "scheduled-jobs" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "scheduled-jobs",
            onClick: function () {
              setTab("scheduled-jobs");
              fetchScheduledJobs();
            },
          },
          I.clock(14),
          "Schedules",
          (scheduledJobs.scheduled_workflow_count || 0) > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(88,166,255,0.2)",
                    color: "var(--accent-blue)",
                    marginLeft: 2,
                  },
                },
                scheduledJobs.scheduled_workflow_count,
              )
            : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "workflows" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "workflows",
            onClick: function () {
              setTab("workflows");
              fetchWorkflowsList();
            },
          },
          I.activity(14),
          "Workflows",
          workflowsList.length > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(88,166,255,0.15)",
                    color: "var(--accent-blue)",
                    marginLeft: 2,
                  },
                },
                workflowsList.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "runner-schedule" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "runner-schedule",
            onClick: function () {
              setTab("runner-schedule");
              fetchRunnerCapacity();
            },
          },
          I.clock(14),
          "Runner Plan",
          runnerCapacity && runnerCapacity.state
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(63,185,80,0.15)",
                    color: "var(--accent-green)",
                    marginLeft: 2,
                  },
                },
                runnerCapacity.state.desired || 0,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "deployment" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "deployment",
            onClick: function () {
              setTab("deployment");
              fetchDeploymentState();
            },
          },
          I.server(14),
          "Deployment",
          deploymentStateLoading ||
            ((deploymentState.rollout_state || {}).machines_attention ||
              0) > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(210,153,34,0.2)",
                    color: "var(--accent-yellow)",
                    marginLeft: 2,
                  },
                },
                deploymentStateLoading
                  ? "\u2026"
                  : (deploymentState.rollout_state || {})
                      .machines_attention,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "local-apps" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "local-apps",
            onClick: function () {
              setTab("local-apps");
              fetchLocalApps();
            },
          },
          I.server(14),
          "Local Tools",
          (function () {
            var apps = localApps.tools || localApps.apps || [];
            var n = apps.filter(localAppNeedsAttention).length;
            return n > 0
              ? h(
                  "span",
                  {
                    className: "section-badge",
                    style: {
                      background: "rgba(210,153,34,0.2)",
                      color: "var(--accent-yellow)",
                      marginLeft: 2,
                    },
                  },
                  n,
                )
              : null;
          })(),
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "credentials" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "credentials",
            onClick: function () {
              setTab("credentials");
              if (!mobileCredentialsViewport) fetchCredentials();
            },
          },
          I.settings(14),
          "Credentials",
          credentialsData.summary && credentialsData.summary.not_ready > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(210,153,34,0.15)",
                    color: "var(--accent-yellow)",
                    marginLeft: 2,
                  },
                },
                credentialsData.summary.not_ready,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" +
              (tab === "fleet-orchestration" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "fleet-orchestration",
            onClick: function () {
              setTab("fleet-orchestration");
              fetchFleetOrchestration();
            },
          },
          I.server(14),
          "Fleet Orchestration",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "assessments" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "assessments",
            onClick: function () {
              setTab("assessments");
              fetchAssessments();
            },
          },
          I.activity(14),
          "Assessments",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "feature-requests" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "feature-requests",
            onClick: function () {
              setTab("feature-requests");
              fetchFeatureRequests();
            },
          },
          I.issue(14),
          "Feature Requests",
          featureRequests.length > 0
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(136,108,228,0.15)",
                    color: "var(--accent-purple)",
                    marginLeft: 2,
                  },
                },
                featureRequests.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "cline-launcher" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "cline-launcher",
            onClick: function () {
              setTab("cline-launcher");
            },
          },
          I.terminal ? I.terminal(14) : I.server(14),
          "Cline Launcher",
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "maxwell" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "maxwell",
            onClick: function () {
              setTab("maxwell");
              fetchMaxwellStatus();
            },
          },
          I.server(14),
          "Maxwell",
          maxwellStatus.status === "running"
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(63,185,80,0.15)",
                    color: "var(--accent-green)",
                    marginLeft: 2,
                  },
                },
                "on",
              )
            : maxwellStatus.status
              ? h(
                  "span",
                  {
                    className: "section-badge",
                    style: {
                      background: "rgba(248,81,73,0.15)",
                      color: "var(--accent-red)",
                      marginLeft: 2,
                    },
                  },
                  "off",
                )
              : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "runner-audit" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "runner-audit",
            onClick: function () {
              setTab("runner-audit");
            },
            title: "Hosted-runner billing audit",
          },
          I.server(14),
          "Runner Audit",
          (runnerAudit.violations && runnerAudit.violations.length > 0)
            ? h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(248,81,73,0.2)",
                    color: "var(--accent-red)",
                    marginLeft: 2,
                  },
                },
                runnerAudit.violations.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "diagnostics" ? " active" : ""),
            onClick: function () {
              setTab("diagnostics");
            },
          },
          I.settings(14),
          "Diagnostics",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "principals" ? " active" : ""),
            onClick: function () {
              setTab("principals");
            },
          },
          I.server(14),
          "Principals",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "settings" ? " active" : ""),
            onClick: function () {
              setTab("settings");
            },
          },
          I.settings(14),
          "Settings",
        ),
      ),
      ), // close app-header__row--primary
      h(
        "div",
        { className: "app-header__row app-header__row--secondary" },
      h(
        "div",
        { className: "header-right" },
        // The "FLEET QUOTA" widget that used to live here was a hardcoded
        // mock (14/20). Removed because it (a) showed fake data with no
        // backing API, (b) had no defined semantics — "quota" wasn't tied
        // to runner schedule limits, busy-count, or anything real, and
        // (c) was obscuring the principal/badge/settings controls in the
        // toolstrip. If a real fleet-capacity indicator is wanted, it
        // belongs in the new shell under frontend/src/shell with a clear
        // data contract (e.g. busy_runners / scheduled_runners derived
        // from /api/fleet/nodes), not as another mock here.
        principal ? h(
          "span",
          { className: "section-badge", style: { background: "rgba(88,166,255,0.15)", color: "var(--accent-blue)" } },
          "Acting as: " + principal.name
        ) : null,
        h(
          "a",
          {
            href: principal ? "#" : "/api/auth/github",
            onClick: principal ? function(e) {
              e.preventDefault();
              legacyFetch("/api/auth/logout", {method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" }})
                .then(function() { window.location.reload(); });
            } : undefined,
            className: "btn",
            style: { textDecoration: "none", marginRight: "12px", height: "24px", lineHeight: "12px", fontSize: "11px" }
          },
          principal ? "Logout" : "Login"
        ),
        h("span", {
          className: "status-dot " + (connected ? "green" : "red"),
        }),
        connected ? "Live" : "Offline",
        h(
          "span",
          { className: "hide-mobile" },
          lastUpdate ? " \u00B7 " + lastUpdate : "",
        ),
        h(
          "span",
          {
            className: "section-badge",
            title: "Queued and running workflows",
          },
          "Queue " +
            ((queue.queued_count || 0) +
              "/" +
              (queue.in_progress_count || 0)),
        ),
        h(
          "span",
          {
            className: "section-badge",
            title:
              watchdog && watchdog.summary
                ? watchdog.summary
                : "WSL keepalive and watchdog state",
          },
          "Keepalive " +
            (watchdog && watchdog.status ? watchdog.status : "unknown"),
        ),
        h(
          "span",
          {
            className: "section-badge",
            title: githubStatus.detail || "GitHub API status",
            style:
              githubStatus.status === "rate_limited" || githubStatus.status === "auth_error"
                ? { background: "rgba(240,136,62,0.18)", color: "var(--accent-orange)" }
                : undefined,
          },
          "GitHub " + (githubStatus.status || "unknown"),
        ),
        h(
          "span",
          {
            className: "section-badge",
            title: deployment.deployed_at || "Deployment revision",
          },
          "Build " + shortSha(deployment.git_sha),
        ),
        h(AlertsCenter, {
          alerts: appAlerts,
          onNavigate: onAlertNavigate,
        }),
        h("button", {
          className: "btn",
          style: { marginLeft: 4, background: asstOpen ? "var(--accent-blue)" : undefined, color: asstOpen ? "var(--text-on-accent)" : undefined },
          onClick: toggleAsst,
          title: "Toggle Chat sidebar",
        }, "💬 Chat"),
        h(QuickDispatchPopover, null),
        h(
          "button",
          {
            className: "btn",
            style: { marginLeft: 4 },
            onClick: function () {
              fetchFleet();
              fetchRepos();
              fetchTests();
              fetchReports();
              fetchQueue();
              fetchMachines();
              fetchLocalApps();
              fetchEnrichedRuns();
              fetchWatchdog();
              fetchDeployment();
              fetchDeploymentState();
              fetchRemediationConfig();
              fetchScheduledJobs();
              fetchRunnerCapacity();
            },
          },
          I.refresh(12),
        ),
      ),
      ), // close app-header__row--secondary
    ),
    h(
      "div",
      { style: { display: "flex", flexDirection: asstPosition === "left" ? "row-reverse" : "row", alignItems: "flex-start", minHeight: "calc(100vh - 56px)" } },
      h(
        "div",
        // In chromeless mode the modern desktop shell (#802) already provides
        // the single `main` landmark, so this inner region drops role="main" to
        // avoid duplicate landmarks.
        { className: "main-content", role: chromeless ? undefined : "main", style: { flex: 1, minWidth: 0 } },
        tab === "overview"
        ? h("div", null,
          h(FleetTab, {
            runners: runners,
            runs: runs,
            system: system,
            stats: stats,
            queue: queue,
            machinesData: machinesData,
            onFleet: onFleet,
            onRunner: onRunner,
            loading: actionLoading,
            watchdog: watchdog,
            deployment: deployment,
            setTab: setTab,             // for hero KPI buttons that navigate to other tabs
            runnerAudit: runnerAudit,   // for hosted-runner billing alert in fleet hero
            onOpenDeployment: function () {
              setTab("deployment");
              fetchDeploymentState();
            },
          }),
          // Issue #863: at-a-glance alarm panel + recent event log on Overview.
          h("div", { className: "section", style: { marginTop: "24px" } },
            h("div", { className: "section-header" },
              h("div", { className: "section-title" },
                I.activity(16),
                "Alarms & Recent Events"
              ),
              h("button", {
                className: "btn",
                style: { marginLeft: "auto" },
                onClick: function () { setTab("events"); },
              }, "Open Event Log")
            ),
            h("div", { className: "section-body" },
              h(OverviewEventSection, { rollupAlerts: appAlerts, onNavigate: onAlertNavigate })
            )
          ),
          h("div", { className: "section", style: { marginTop: "24px" } },
            h("div", { className: "section-header", style: { background: "var(--grad-fair)", color: "white" } },
              h("div", { className: "section-title" },
                I.activity(16),
                "Fair Sharing & Active Leases"
              ),
              h("span", { className: "section-badge", style: { background: "rgba(255,255,255,0.2)", color: "white" } }, "Wave 3")
            ),
            h("div", { className: "section-body" },
              h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "16px" } },
                h("div", { className: "glass-card", style: { padding: "16px" } },
                  h("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: "12px" } },
                    h("span", { style: { fontWeight: "700", fontSize: "14px" } }, "USER: dieterolson"),
                    h("span", { className: "conclusion-badge in_progress" }, "Active")
                  ),
                  h("div", { className: "metric-row" },
                    h("span", { className: "metric-label" }, "Runner:"),
                    h("span", { className: "metric-value" }, "ubuntu-latest-4xlarge")
                  ),
                  h("div", { className: "metric-row" },
                    h("span", { className: "metric-label" }, "Lease Time:"),
                    h("span", { className: "metric-value" }, "45m / 2h")
                  ),
                  h("div", { className: "progress-bar", style: { margin: "8px 0" } },
                    h("div", { className: "progress-fill blue", style: { width: "37%" } })
                  ),
                  h("button", { className: "btn btn-red", style: { width: "100%", marginTop: "8px", justifyContent: "center" }, "aria-label": "Relinquish runner" }, "Relinquish Runner")
                ),
                h("div", { className: "glass-card", style: { padding: "16px" } },
                  h("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: "12px" } },
                    h("span", { style: { fontWeight: "700", fontSize: "14px" } }, "USER: jules-bot"),
                    h("span", { className: "conclusion-badge success" }, "Idle")
                  ),
                  h("div", { className: "metric-row" },
                    h("span", { className: "metric-label" }, "Runner:"),
                    h("span", { className: "metric-value" }, "windows-2022-standard")
                  ),
                  h("div", { className: "metric-row" },
                    h("span", { className: "metric-label" }, "Quota Left:"),
                    h("span", { className: "metric-value" }, "Unlimited")
                  ),
                  h("div", { className: "progress-bar", style: { margin: "8px 0" } },
                    h("div", { className: "progress-fill purple", style: { width: "100%" } })
                  ),
                  h("button", { className: "btn", style: { width: "100%", marginTop: "8px", justifyContent: "center" }, "aria-label": "View runner logs" }, "View Logs")
                )
              )
            )
          )
        )
        : tab === "deployment"
          ? h(DeploymentTab, {
              data: deploymentState,
              loading: deploymentStateLoading,
              onRefresh: fetchDeploymentState,
              onOpenFleet: function () {
                setTab("overview");
                fetchFleet();
              },
            })
          : tab === "agent-dispatch"
            ? h(AgentDispatchPage)
            : tab === "conductor"
            ? h(Conductor)
            : tab === "remediation"
            ? h(RemediationTab, {
                config: remediationConfig,
                workflows: remediationWorkflows,
                runs: enrichedRuns.length ? enrichedRuns : runs,
                loading: remediationLoading,
                error: remediationError,
                selectedRunId: remediationSelectedRunId,
                setSelectedRunId: setRemediationSelectedRunId,
                provider: remediationProvider,
                setProvider: setRemediationProvider,
                model: remediationModel,
                setModel: setRemediationModel,
                plan: remediationPlan,
                dispatchState: remediationDispatchState,
                onRefresh: fetchRemediationConfig,
                onSaveConfig: saveRemediationConfig,
                onPreview: previewRemediation,
                onDispatch: dispatchRemediation,
                history: remediationHistory,
                principalName: principal && principal.name,
              })
            : isAnalysisTabKey(tab)
              ? h(AnalysisTab, {
                  activeTab: tab,
                  runs: enrichedRuns,
                  runners: runners,
                  reports: reports,
                  reportsLoading: reportsLoading,
                })
              : tab === "queue"
                ? h(QueueTab, {
                    queue: queue,
                    loading: queueLoading,
                    onRefresh: fetchQueue,
                  })
                : tab === "machines"
                  ? h(MachinesTab, {
                      data: machinesData,
                      loading: machinesLoading,
                      runners: runners,
                    })
                  : tab === "org"
                    ? h(OrgTab, {
                        repos: repos,
                        loading: reposLoading,
                        stats: stats,
                      })
                    : tab === "tests"
                      ? h(TestsTab, {
                          testRepos: testRepos,
                          loading: testsLoading,
                          ciResults: ciResults,
                        })
                      : tab === "scheduled-jobs"
                            ? h(ScheduledJobs, null)
                            : tab === "workflows"
                              ? h(WorkflowsTab, {
                                  workflows: workflowsList,
                                  loading: workflowsListLoading,
                                  error: workflowsListError,
                                  onDispatch: dispatchWorkflow,
                                  onRefresh: fetchWorkflowsList,
                                })
                              : tab === "runner-schedule"
                                ? h(RunnerScheduleTab, {
                                    data: runnerCapacity,
                                    loading: runnerCapacityLoading,
                                    onRefresh: fetchRunnerCapacity,
                                    onSave: saveRunnerCapacity,
                                  })
                                : tab === "local-apps"
                                  ? h(LocalAppsTab, {
                                        key: "local-apps-boundary",
                                        data: localApps,
                                        loading: localAppsLoading,
                                        onRefresh: fetchLocalApps,
                                      })
                                  : tab === "credentials"
                                    ? h(CredentialsTab, {
                                        probes:
                                          credentialsData.probes || [],
                                        summary:
                                          credentialsData.summary || {},
                                        loading: credentialsLoading,
                                        error: credentialsError,
                                        onRefresh: fetchCredentials,
                                        onSetKey: setCredentialKey,
                                        mobile: mobileCredentialsViewport,
                                      })
                                  : tab === "fleet-orchestration"
                                    ? h(FleetOrchestrationTab, {
                                        data: fleetOrchData,
                                        loading: fleetOrchLoading,
                                        error: fleetOrchError,
                                        onRefresh:
                                          fetchFleetOrchestration,
                                        onDispatch: orchDispatch,
                                        onDeploy: orchDeploy,
                                      })
                                    : tab === "assessments"
                                      ? h(AssessmentsTab, {
                                          repos: repos,
                                          scores: assessmentScores,
                                          loading: assessmentLoading,
                                          error: assessmentError,
                                          onDispatch: dispatchAssessment,
                                          onRefresh: fetchAssessments,
                                        })
                                      : tab === "feature-requests"
                                        ? h(FeatureRequestsTab, {
                                            repos: repos,
                                            requests: featureRequests,
                                            templates: promptTemplates,
                                            standards: featureStandards,
                                            loading: featureRequestsLoading,
                                            promptNotes: promptNotes,
                                            onDispatch: dispatchFeatureRequest,
                                            onSaveTemplate: savePromptTemplate,
                                            onSavePromptNotes: updatePromptNotes,
                                            onRefresh: fetchFeatureRequests,
                                          })
                                        : tab === "maxwell"
                                          ? h(MaxwellTab, {
                                              status: maxwellStatus,
                                              loading: maxwellLoading,
                                              error: maxwellError,
                                              onRefresh:
                                                fetchMaxwellStatus,
                                              onControl: maxwellControl,
                                            })
                                          : tab === "cline-launcher"
                                            ? h(ClineLauncherTab, null)
                                            : tab === "diagnostics"
                                              ? h(DiagnosticsTab, null)
                                              : tab === "runner-audit"
                                                ? h(RunnerAudit, {
                                                    audit: runnerAudit,
                                                    onRefresh: triggerRunnerAuditRefresh,
                                                  })
                                                : tab === "principals"
                                                  ? h(PrincipalsTab, null)
                                                  : tab === "settings"
                                                    ? h(ThemeSettings, null)
                                                    : tab === "linear-setup"
                                                      ? h(LinearSetup, null)
                                                      : tab === "push-settings"
                                                        ? h(PushSettings, null)
                                                        : tab === "events"
                                                          ? h(EventsTab, { rollupAlerts: appAlerts, onNavigate: onAlertNavigate })
                                                          : null,
      ),
      h(AssistantSidebar, { currentTab: tab, open: asstOpen, onToggle: toggleAsst }),
    ),
    recoveryModal && recoveryModal.visible
      ? h(RecoveryDialog, { onClose: function () { setRecoveryModal(null); } })
      : null,
    h(SessionExpiredDialog, {
      open: sessionExpiredOpen,
      onClose: function () { setSessionExpiredOpen(false); },
    }),
    h(DashboardHelp, { currentTab: tab }),
  );
}

export default App;
