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
import { VoiceInputButton } from "../components/VoiceInputButton"
import { ThemeSettings } from "../components/ThemeSettings"

var h = React.createElement;
var SERVICE_WORKER_CACHE_DENYLIST = [/^\/api\/credentials(?:\/|$)/];

function prefersReducedMotion() {
  return window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)").matches : false;
}

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

function Collapse(p) {
  var r = React.useState(p.defaultOpen !== false);
  var o = r[0],
    s = r[1];
  return h(
    "div",
    { className: "section" },
    h(
      "div",
      {
        className: "section-header",
        onClick: function () {
          s(!o);
        },
      },
      h(
        "div",
        { className: "section-title" },
        p.icon,
        p.title,
        p.badge
          ? h(Badge, { tone: "neutral" }, p.badge)
          : null,
      ),
      h(
        "span",
        { className: "chevron" + (o ? " open" : "") },
        I.chevronDown(),
      ),
    ),
    h(
      "div",
      { className: "section-body" + (o ? "" : " collapsed") },
      p.children,
    ),
  );
}

function SubTabs(p) {
  var tabs = p.tabs || [];
  var storageKey = p.storageKey;
  var initialKey = storageKey
    ? (localStorage.getItem(storageKey) || tabs[0] && tabs[0].key)
    : (tabs[0] && tabs[0].key);
  var ia = React.useState(initialKey);
  var internalActive = ia[0],
    setInternalActive = ia[1];
  var activeKey = p.activeKey !== undefined ? p.activeKey : internalActive;
  function handleChange(key) {
    if (p.activeKey === undefined) {
      setInternalActive(key);
    }
    if (storageKey) {
      try { localStorage.setItem(storageKey, key); } catch (e) {}
    }
    if (p.onChange) p.onChange(key);
  }
  // a11y (#833): the sub-tab strip is an ARIA tablist. Roving focus — only the
  // active tab is tabbable; ←/→/Home/End move between tabs (WAI-ARIA tabs).
  var enabledKeys = tabs.filter(function (t) { return !t.disabled; }).map(function (t) { return t.key; });
  function onStripKeyDown(e) {
    if (enabledKeys.length === 0) return;
    var idx = enabledKeys.indexOf(activeKey);
    var next = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      next = enabledKeys[(idx + 1 + enabledKeys.length) % enabledKeys.length];
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      next = enabledKeys[(idx - 1 + enabledKeys.length) % enabledKeys.length];
    } else if (e.key === "Home") {
      next = enabledKeys[0];
    } else if (e.key === "End") {
      next = enabledKeys[enabledKeys.length - 1];
    }
    if (next != null) {
      e.preventDefault();
      handleChange(next);
    }
  }
  return h(
    "div",
    { className: "subtabs" + (p.className ? " " + p.className : "") },
    h(
      "div",
      {
        className: "subtabs-strip",
        role: "tablist",
        "aria-label": p.ariaLabel || p.label || "Section tabs",
        onKeyDown: onStripKeyDown,
      },
      tabs.map(function (tab) {
        var selected = activeKey === tab.key;
        return h(
          "button",
          {
            key: tab.key,
            className: "subtab" + (selected ? " active" : ""),
            role: "tab",
            "aria-selected": selected ? "true" : "false",
            "aria-disabled": tab.disabled ? "true" : undefined,
            tabIndex: selected ? 0 : -1,
            disabled: tab.disabled || false,
            onClick: function () { if (!tab.disabled) handleChange(tab.key); },
          },
          tab.label,
          tab.badge != null
            ? h(Badge, { tone: selected ? "info" : "neutral", size: "sm" }, tab.badge)
            : null,
        );
      }),
    ),
    p.rightBadge ? h("div", { className: "subtabs-right" }, p.rightBadge) : null,
  );
}

function isAnalysisTabKey(key) {
  return ["analysis", "stats", "performance", "reports", "history"].indexOf(key) >= 0;
}

function Stat(p) {
  return h(
    "div",
    { className: "stat-card" },
    h("div", { className: "stat-label" }, p.label),
    h(
      "div",
      { className: "stat-value", style: { color: p.color || "inherit" } },
      p.value,
    ),
    p.sub
      ? h(
          "div",
          {
            className: "stat-sub",
            // Hover for the full text when the sub line has been truncated.
            title: p.subTitle || (typeof p.sub === "string" ? p.sub : ""),
          },
          p.sub,
        )
      : null,
  );
}

function canonicalMachineName(name) {
  var raw = String(name || "").trim();
  var key = raw.toLowerCase().replace(/[^a-z0-9]/g, "");
  var aliases = {
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

function parseRunnerName(name) {
  var s = String(name || "");
  var match = s.match(/^d-sorg-local-(.+)-(\d+)$/);
  if (match) {
    return { machine: canonicalMachineName(match[1]), number: Number(match[2]) };
  }
  var matlabMatch = s.match(/^(.+)-MATLAB$/i);
  if (matlabMatch) {
    return { machine: canonicalMachineName(matlabMatch[1]), number: 9998 };
  }
  return { machine: "Unknown", number: 999999 };
}

function runnerSort(a, b) {
  var pa = parseRunnerName(a.name);
  var pb = parseRunnerName(b.name);
  if (pa.machine !== pb.machine) {
    if (pa.machine === "ControlTower") return -1;
    if (pb.machine === "ControlTower") return 1;
    return pa.machine.localeCompare(pb.machine);
  }
  return pa.number - pb.number;
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

function nodeHasSystemMetrics(n) {
  var sys = (n && n.system) || {};
  return !!(
    (sys.cpu && sys.cpu.percent != null) ||
    (sys.memory && sys.memory.percent != null) ||
    (sys.disk && sys.disk.percent != null)
  );
}

function nodeQualityScore(n) {
  var score = 0;
  if (!n) return score;
  if (n.is_local) score += 100;
  if (n.dashboard_reachable !== false) score += 40;
  if (n.online) score += 20;
  if (nodeHasSystemMetrics(n)) score += 60;
  if (n.role === "hub") score += 5;
  if (n.role === "runner_pool") score -= 10;
  return score;
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

function sortStateNext(current, key) {
  if (current && current.key === key) {
    return { key: key, dir: current.dir === "asc" ? "desc" : "asc" };
  }
  return { key: key, dir: "asc" };
}

function normalizeSortValue(value) {
  if (value == null) return "";
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  var text = String(value);
  var asDate = Date.parse(text);
  if (
    !Number.isNaN(asDate) &&
    /\d{4}-\d{2}-\d{2}|T\d{2}:/.test(text)
  ) {
    return asDate;
  }
  var numeric = Number(text.replace(/[^0-9.-]/g, ""));
  if (text.trim() && !Number.isNaN(numeric) && /[0-9]/.test(text)) {
    return numeric;
  }
  return text.toLowerCase();
}

function sortRows(rows, sort, accessors) {
  if (!sort || !sort.key || !accessors || !accessors[sort.key]) {
    return rows.slice();
  }
  var dir = sort.dir === "desc" ? -1 : 1;
  return rows
    .map(function (row, index) {
      return { row: row, index: index };
    })
    .sort(function (a, b) {
      var av = normalizeSortValue(accessors[sort.key](a.row));
      var bv = normalizeSortValue(accessors[sort.key](b.row));
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return a.index - b.index;
    })
    .map(function (entry) {
      return entry.row;
    });
}

function SortTh(p) {
  var active = p.sort && p.sort.key === p.sortKey;
  var dir = active ? p.sort.dir : "";
  var props = Object.assign({}, p.thProps || {}, {
    className:
      ((p.thProps && p.thProps.className) || "") +
      " sortable" +
      (active ? " active" : ""),
    // a11y (#833): a <th> already has the implicit `columnheader` role, which
    // is the only role on which `aria-sort` is permitted — applying
    // role="button" here makes aria-sort an unsupported attribute (axe
    // aria-allowed-attr violation). Keep the native role; expose interactivity
    // via tabIndex + Enter/Space handling and an aria-label that announces the
    // sort action to assistive tech.
    scope: (p.thProps && p.thProps.scope) || "col",
    tabIndex: 0,
    "aria-sort": active
      ? dir === "desc"
        ? "descending"
        : "ascending"
      : "none",
    "aria-label": "Sort by " + p.label,
    title: "Sort by " + p.label,
    onClick: function () {
      p.setSort(sortStateNext(p.sort, p.sortKey));
    },
    onKeyDown: function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        p.setSort(sortStateNext(p.sort, p.sortKey));
      }
    },
  });
  return h(
    "th",
    props,
    h(
      "span",
      { className: "sort-heading" },
      p.label,
      h("span", { className: "sort-indicator" }, active ? (dir === "desc" ? "↓" : "↑") : "↕"),
    ),
  );
}

function shortSha(sha) {
  return sha ? String(sha).slice(0, 7) : "unknown";
}

// ════════════════════════ FLEET TAB ════════════════════════
function offlineReasonLabel(reason) {
  return {
    wsl_connection_lost: "WSL/dashboard connection lost",
    resource_monitoring: "Taken offline by resource monitoring",
    computer_offline: "Computer unreachable",
    dashboard_unhealthy: "Dashboard unhealthy",
    dashboard_not_deployed: "Dashboard not deployed",
    runner_service_offline: "Runner services offline",
    unknown: "Unknown",
  }[reason || "unknown"];
}

function visibilitySnapshot(node, onlineCount) {
  var system = node.system || {};
  var hasSystemMetrics = Object.keys(system).length > 0;
  var hasRunnerTelemetry = !!node.online || onlineCount > 0;
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
      detail:
        "Dashboard is reachable, but runner registrations are offline.",
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

function resolveVisibility(node, onlineCount) {
  var computed = visibilitySnapshot(node, onlineCount);
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

// ════════════════════════ MACHINES TAB ════════════════════════
function MachineCard(p) {
  var n = p.node;
  var relatedNodes = p.relatedNodes || n.related_nodes || [n];
  var machineRunners = p.machineRunners || [];
  var sys = n.system || {};
  var busyCount = machineRunners.filter(function (r) {
    return r.busy;
  }).length;
  var onlineCount = machineRunners.filter(function (r) {
    return r.status === "online";
  }).length;
  var visibility = resolveVisibility(n, onlineCount);
  // Machine is "live" if it has any online runners OR its dashboard is reachable.
  var isLive = !!n.online || onlineCount > 0;
  var dashboardReachable =
    n.dashboard_reachable !== false && !!sys.uptime_seconds;
  var uptimeStr = (function () {
    var s = sys.uptime_seconds;
    if (!s) return dashboardReachable ? "-" : "dashboard not deployed";
    var hr = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    if (hr > 24) return Math.floor(hr / 24) + "d " + (hr % 24) + "h";
    return hr + "h " + m + "m";
  })();
  var mColors = {
    ControlTower: "var(--accent-purple)",
    DeskComputer: "var(--accent-blue)",
    OGLaptop: "var(--accent-orange)",
  };
  var mColor = mColors[n.name] || "var(--accent-blue)";
  var dotClass = isLive
    ? dashboardReachable
      ? "green"
      : "yellow"
    : "red";
  var offlineReason =
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
            className:
              "telemetry-badge " + (visibility.state || "offline"),
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
            machineRunners.map(function (r) {
              var color = r.busy
                ? "var(--accent-yellow)"
                : r.status === "online"
                  ? "var(--accent-green)"
                  : "var(--accent-red)";
              var label = r.name.split("-").pop();
              var title =
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

function MachinesTab(p) {
  var d = p.data || {};
  var loading = p.loading;
  var allRunners = p.runners || [];
  var nodes = d.nodes || [];
  var online = d.online_count || 0;
  // Group runners by machine name
  var runnersByMachine = {};
  allRunners.forEach(function (r) {
    var machine = parseRunnerName(r.name).machine;
    if (!runnersByMachine[machine]) runnersByMachine[machine] = [];
    runnersByMachine[machine].push(r);
  });
  Object.keys(runnersByMachine).forEach(function (name) {
    runnersByMachine[name] = runnersByMachine[name]
      .slice()
      .sort(runnerSort);
  });
  var totalBusy = allRunners.filter(function (r) {
    return r.busy;
  }).length;
  var totalOnline = allRunners.filter(function (r) {
    return r.status === "online";
  }).length;

  var nodesByPhysicalName = {};
  nodes.forEach(function (n) {
    var physicalName = canonicalMachineName(n.parent_machine || n.name);
    var key = physicalName.toLowerCase();
    if (!nodesByPhysicalName[key]) nodesByPhysicalName[key] = [];
    nodesByPhysicalName[key].push(n);
  });

  // Build machine list from both runners and registry/fleet nodes. Runner pool
  // entries such as ControlTower-NVMe and ControlTower-SSD are folded into the
  // same physical machine card, and the best live telemetry node wins.
  var machineNameSet = {};
  Object.keys(runnersByMachine).forEach(function (name) {
    machineNameSet[canonicalMachineName(name)] = true;
  });
  Object.keys(nodesByPhysicalName).forEach(function (key) {
    var group = nodesByPhysicalName[key] || [];
    var display = canonicalMachineName(
      (group[0] && (group[0].parent_machine || group[0].name)) || key,
    );
    machineNameSet[display] = true;
  });
  var machineNames = Object.keys(machineNameSet).sort(function (a, b) {
    return a === "ControlTower"
      ? -1
      : b === "ControlTower"
        ? 1
        : a.localeCompare(b);
  });

  var allNodes = machineNames.map(function (name) {
    var related = (nodesByPhysicalName[name.toLowerCase()] || []).slice();
    related.sort(function (a, b) {
      return nodeQualityScore(b) - nodeQualityScore(a);
    });
    var node = related[0];
    if (node) {
      return Object.assign({}, node, {
        name: name,
        related_nodes: related,
        physical_machine: name,
      });
    }
    // Create a stub node from runner data (no backend entry at all)
    var mrs = runnersByMachine[name] || [];
    var mOnline = mrs.filter(function (r) {
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
  var gpuNodes = allNodes.filter(function (n) {
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
          allNodes.filter(function (n) {
            return n.online;
          }).length +
          "/" +
          allNodes.length +
          " online",
        color:
          allNodes.every(function (n) {
            return n.online;
          }) && allNodes.length > 0
            ? "var(--accent-green)"
            : "var(--accent-yellow)",
      }),
      h(Stat, {
        label: "Total Runners",
        value: allRunners.length,
        sub: totalOnline + " online, " + totalBusy + " busy",
        color:
          totalBusy > 0 ? "var(--accent-yellow)" : "var(--accent-green)",
      }),
      h(Stat, {
        label: "GPU Nodes",
        value: gpuNodes.length,
        color: gpuNodes.length > 0 ? "var(--accent-purple)" : "inherit",
        sub:
          gpuNodes
            .map(function (n) {
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
          allNodes.map(function (n) {
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


// ════════════════════════ SYSTEM RESOURCES PANEL ════════════════════════
function collectStorageDevices(system, relatedNodes) {
  var devices = [];
  var seen = {};
  function addDevice(device, fallbackLabel) {
    if (!device) return;
    var label = device.label || fallbackLabel || device.path || "Storage";
    var key = [label, device.kind || "", device.path || ""].join("|");
    if (seen[key]) return;
    seen[key] = true;
    devices.push(Object.assign({}, device, { label: label }));
  }
  function addFromSystem(sys, prefix) {
    var disk = (sys && sys.disk) || {};
    (disk.storage_devices || []).forEach(function (device) {
      addDevice(device, device.label || prefix);
    });
    if ((!disk.storage_devices || disk.storage_devices.length === 0) && disk.windows_host) {
      addDevice(disk.windows_host, prefix ? prefix + " Disk" : "Host Disk");
    }
    if ((!disk.storage_devices || disk.storage_devices.length === 0) && disk.percent != null) {
      addDevice(disk, prefix ? prefix + " WSL" : "WSL Disk");
    }
  }
  addFromSystem(system, "");
  (relatedNodes || []).forEach(function (node) {
    addFromSystem(node.system, node.name);
  });
  return devices;
}

function StorageDeviceMetric(p) {
  var device = p.device || {};
  var pct = boundedPercent(device.percent || 0);
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

function SystemResourcesPanel(p) {
  var sys = p.system || {};
  var relatedNodes = p.relatedNodes || [];
  var cpu = sys.cpu || {};
  var mem = sys.memory || {};
  var disk = sys.disk || {};
  var diskPressure = disk.pressure || {};
  var storageDevices = collectStorageDevices(sys, relatedNodes);
  var net = sys.network || {};
  var gpus = (sys.gpu && sys.gpu.gpus) || [];
  var rprocs = sys.runner_processes || [];
  var procSortState = React.useState({ key: "runner", dir: "asc" });
  var procSort = procSortState[0],
    setProcSort = procSortState[1];
  var procAccessors = {
    runner: function (rp) {
      return rp.runner_num || 0;
    },
    status: function (rp) {
      return rp.status || "";
    },
    cpu: function (rp) {
      return rp.cpu_percent || 0;
    },
    memory: function (rp) {
      return rp.memory_mb || 0;
    },
    procs: function (rp) {
      return rp.process_count || 0;
    },
  };
  var sortedRprocs = sortRows(rprocs, procSort, procAccessors);
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
      "System metrics unavailable \u2014 dashboard port forwarding needed on this machine",
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
            cpu.per_cpu_percent.map(function (v, i) {
              return h(
                "div",
                {
                  className: "cpu-core",
                  key: i,
                  style: {
                    background: cpuColor(v),
                    color: v > 50 ? "var(--text-on-accent)" : "var(--text-secondary)",
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
              (function() {
                var usedPct = mem.total_gb ? Math.round((1 - mem.available_gb / mem.total_gb) * 100) : Math.round(mem.percent || 0);
                var label = mem.source === "wsl" ? "WSL" : "Host";
                return label + " " + mem.used_gb + " / " + mem.total_gb + " GB (" + usedPct + "%)";
              })(),
            ),
          ),
          h(
            "div",
            { className: "progress-bar" },
            h("div", {
              className: "progress-fill " + pColor(mem.total_gb ? Math.round((1 - mem.available_gb / mem.total_gb) * 100) : Math.round(mem.percent || 0)),
              style: { width: (mem.total_gb ? Math.round((1 - mem.available_gb / mem.total_gb) * 100) : Math.round(mem.percent || 0)) + "%" },
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
            cpu.load_avg_1m +
              " / " +
              cpu.load_avg_5m +
              " / " +
              cpu.load_avg_15m,
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
            "\u2191 " +
              formatBytes(net.bytes_sent) +
              "  \u2193 " +
              formatBytes(net.bytes_recv),
          ),
        )
      : null,
    gpus.length > 0
      ? gpus.map(function (g, i) {
          return h(
            "div",
            { className: "gpu-card", key: i },
            h("div", { className: "gpu-name" }, "\uD83C\uDFAE ", g.name),
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
              h(
                "span",
                { className: "metric-value" },
                g.gpu_util_percent + "%",
              ),
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
                    color:
                      g.temp_c > 80 ? "var(--accent-red)" : "inherit",
                  },
                },
                g.temp_c + "\u00B0C",
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
              sortedRprocs.map(function (rp) {
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
                          (rp.status === "running"
                            ? "online"
                            : "offline"),
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

// ════════════════════════ HISTORY TAB ════════════════════════
var MACHINE_COLORS = {
  ControlTower: "var(--accent-purple)",
  DeskComputer: "var(--accent-blue)",
  Oglaptop: "var(--accent-orange)",
  GitHub: "var(--text-muted)",
};
function HistoryTab(props) {
  var runs = props.runs || [];
  var runners = props.runners || [];
  var fs = React.useState("all");
  var filter = fs[0],
    setFilter = fs[1];
  var sortState = React.useState({ key: "when", dir: "desc" });
  var historySort = sortState[0],
    setHistorySort = sortState[1];
  var filtered = runs.filter(function (r) {
    if (filter === "all") return true;
    if (filter === "success") return r.conclusion === "success";
    if (filter === "failure") return r.conclusion === "failure";
    if (filter === "running") return r.status === "in_progress";
    if (filter === "cancelled") return r.conclusion === "cancelled";
    return true;
  });
  function dur(r) {
    if (!r.run_started_at || !r.updated_at) return "-";
    var ms = new Date(r.updated_at) - new Date(r.run_started_at);
    var s = Math.floor(ms / 1000);
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "m " + (s % 60) + "s";
    return Math.floor(m / 60) + "h " + (m % 60) + "m";
  }
  function ago(d) {
    if (!d) return "-";
    var s = Math.floor((Date.now() - new Date(d)) / 1000);
    if (s < 60) return s + "s ago";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    var hr = Math.floor(m / 60);
    if (hr < 24) return hr + "h ago";
    return Math.floor(hr / 24) + "d ago";
  }
  function statusIcon(r) {
    if (r.status === "in_progress")
      return h(
        "span",
        { style: { color: "var(--accent-yellow)" } },
        "\u25CF",
      );
    if (r.conclusion === "success")
      return h(
        "span",
        { style: { color: "var(--accent-green)" } },
        "\u2713",
      );
    if (r.conclusion === "failure")
      return h(
        "span",
        { style: { color: "var(--accent-red)" } },
        "\u2717",
      );
    if (r.conclusion === "cancelled")
      return h(
        "span",
        { style: { color: "var(--text-muted)" } },
        "\u25CB",
      );
    return h("span", { style: { color: "var(--text-muted)" } }, "\u2022");
  }
  var historyAccessors = {
    status: function (r) {
      return r.status === "in_progress" ? "running" : r.conclusion || "";
    },
    workflow: function (r) {
      return r.name;
    },
    repository: function (r) {
      return (r.repository || {}).name || "";
    },
    branch: function (r) {
      return r.head_branch;
    },
    machine: function (r) {
      return r.machine_name || "";
    },
    duration: function (r) {
      if (!r.run_started_at || !r.updated_at) return 0;
      return new Date(r.updated_at) - new Date(r.run_started_at);
    },
    when: function (r) {
      return r.created_at || r.updated_at || "";
    },
  };
  var sortedFiltered = sortRows(filtered, historySort, historyAccessors);
  var counts = {
    all: runs.length,
    success: runs.filter(function (r) {
      return r.conclusion === "success";
    }).length,
    failure: runs.filter(function (r) {
      return r.conclusion === "failure";
    }).length,
    running: runs.filter(function (r) {
      return r.status === "in_progress";
    }).length,
    cancelled: runs.filter(function (r) {
      return r.conclusion === "cancelled";
    }).length,
  };
  return h(
    "div",
    null,
    h(
      "div",
      {
        style: {
          display: "flex",
          gap: 8,
          marginBottom: 16,
          flexWrap: "wrap",
        },
      },
      ["all", "success", "failure", "running", "cancelled"].map(
        function (f) {
          return h(
            "button",
            {
              key: f,
              className: "btn" + (filter === f ? " active" : ""),
              onClick: function () {
                setFilter(f);
              },
              style: {
                background:
                  filter === f ? "var(--accent-blue)" : "var(--bg-card)",
                color: filter === f ? "white" : "var(--text-secondary)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "6px 14px",
                cursor: "pointer",
                fontSize: 13,
              },
            },
            f.charAt(0).toUpperCase() + f.slice(1),
            " ",
            h("span", { style: { opacity: 0.6 } }, "(" + counts[f] + ")"),
          );
        },
      ),
    ),
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
            label: "",
            sortKey: "status",
            sort: historySort,
            setSort: setHistorySort,
            thProps: { style: { width: 30 } },
          }),
          h(SortTh, {
            label: "Workflow",
            sortKey: "workflow",
            sort: historySort,
            setSort: setHistorySort,
          }),
          h(SortTh, {
            label: "Repository",
            sortKey: "repository",
            sort: historySort,
            setSort: setHistorySort,
          }),
          h(SortTh, {
            label: "Branch",
            sortKey: "branch",
            sort: historySort,
            setSort: setHistorySort,
          }),
          h(SortTh, {
            label: "Machine",
            sortKey: "machine",
            sort: historySort,
            setSort: setHistorySort,
          }),
          h(SortTh, {
            label: "Duration",
            sortKey: "duration",
            sort: historySort,
            setSort: setHistorySort,
          }),
          h(SortTh, {
            label: "When",
            sortKey: "when",
            sort: historySort,
            setSort: setHistorySort,
          }),
        ),
      ),
      h(
        "tbody",
        null,
        sortedFiltered.slice(0, 50).map(function (r) {
          var machine = r.machine_name || "-";
          var mColor = MACHINE_COLORS[machine] || "var(--text-muted)";
          var repo = (r.repository || {}).name || "?";
          return h(
            "tr",
            {
              key: r.id,
              style: { cursor: "pointer" },
              onClick: function () {
                if (r.html_url) safeOpen(r.html_url);
              },
            },
            h("td", null, statusIcon(r)),
            h(
              "td",
              null,
              h(
                "span",
                {
                  style: {
                    fontWeight: 500,
                    color: "var(--text-primary)",
                  },
                },
                r.name || "?",
              ),
            ),
            h(
              "td",
              null,
              h(
                "span",
                {
                  style: { color: "var(--text-secondary)", fontSize: 13 },
                },
                repo,
              ),
            ),
            h(
              "td",
              null,
              h(
                "span",
                { style: { color: "var(--text-muted)", fontSize: 13 } },
                r.head_branch || "-",
              ),
            ),
            h(
              "td",
              null,
              h(
                "span",
                {
                  style: {
                    background: mColor + "22",
                    color: mColor,
                    padding: "2px 8px",
                    borderRadius: 10,
                    fontSize: 12,
                    fontWeight: 600,
                  },
                },
                machine,
              ),
            ),
            h(
              "td",
              null,
              h(
                "span",
                { style: { color: "var(--text-muted)", fontSize: 13 } },
                dur(r),
              ),
            ),
            h(
              "td",
              null,
              h(
                "span",
                { style: { color: "var(--text-muted)", fontSize: 13 } },
                ago(r.created_at),
              ),
            ),
          );
        }),
      ),
    ),
    filtered.length === 0
      ? h(
          "div",
          {
            style: {
              textAlign: "center",
              color: "var(--text-muted)",
              padding: 40,
            },
          },
          "No workflow runs match this filter",
        )
      : null,
  );
}


// ════════════════════════ PRs SUB-TAB ════════════════════════
function PRsSubTab() {
  // ── State ─────────────────────────────────────────────────────────────
  var prs_s = React.useState([]);
  var prs = prs_s[0], setPrs = prs_s[1];
  var loading_s = React.useState(false);
  var loading = loading_s[0], setLoading = loading_s[1];
  var error_s = React.useState(null);
  var fetchError = error_s[0], setFetchError = error_s[1];

  // Filters
  var rf = React.useState("");
  var repoFilter = rf[0], setRepoFilter = rf[1];
  var af = React.useState("");
  var authorFilter = af[0], setAuthorFilter = af[1];
  var df = React.useState(true);
  var showDrafts = df[0], setShowDrafts = df[1];

  // Selection
  var sel_s = React.useState({});
  var selected = sel_s[0], setSelected = sel_s[1];

  // Sort
  var sort_s = React.useState({ key: "age", dir: "asc" });
  var sort = sort_s[0], setSort = sort_s[1];

  // Dispatch modal
  var modal_s = React.useState(null);
  var dispatchModal = modal_s[0], setDispatchModal = modal_s[1];
  var dispatching_s = React.useState(false);
  var dispatching = dispatching_s[0], setDispatching = dispatching_s[1];
  var dispatchMsg_s = React.useState(null);
  var dispatchMsg = dispatchMsg_s[0], setDispatchMsg = dispatchMsg_s[1];

  // Modal fields
  var modalProvider_s = React.useState("jules_api");
  var modalProvider = modalProvider_s[0], setModalProvider = modalProvider_s[1];
  var modalPrompt_s = React.useState("");
  var modalPrompt = modalPrompt_s[0], setModalPrompt = modalPrompt_s[1];

  // ── Data fetch ────────────────────────────────────────────────────────
  function fetchPRs() {
    setLoading(true);
    setFetchError(null);
    legacyFetch("/api/prs?limit=2000")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        var list = Array.isArray(data) ? data : (data.prs || data.items || []);
        setPrs(list);
        setSelected({});
      })
      .catch(function (err) {
        setFetchError("Failed to load PRs: " + err.message);
      })
      .finally(function () {
        setLoading(false);
      });
  }

  React.useEffect(function () { fetchPRs(); }, []);

  // ── Derived / filtered list ───────────────────────────────────────────
  var filtered = prs.filter(function (pr) {
    if (!showDrafts && pr.draft) return false;
    if (repoFilter) {
      var repo = (pr.repo || pr.repository || pr.full_name || "").toLowerCase();
      if (!repo.includes(repoFilter.toLowerCase())) return false;
    }
    if (authorFilter) {
      var author = (pr.author || (pr.user && pr.user.login) || pr.login || "").toLowerCase();
      if (!author.includes(authorFilter.toLowerCase())) return false;
    }
    return true;
  });

  // Sort
  var sortAccessors = {
    repo: function (pr) { return pr.repo || pr.repository || pr.full_name || ""; },
    number: function (pr) { return pr.number || pr.pr_number || 0; },
    title: function (pr) { return pr.title || ""; },
    author: function (pr) { return pr.author || (pr.user && pr.user.login) || ""; },
    age: function (pr) { return pr.age_hours != null ? pr.age_hours : (pr.created_at ? (Date.now() - new Date(pr.created_at).getTime()) / 3600000 : 0); },
  };
  var sortedPRs = sortRows(filtered, sort, sortAccessors);

  // ── Selection helpers ─────────────────────────────────────────────────
  var visibleIds = sortedPRs.map(function (pr) { return String(pr.number || pr.pr_number || pr.id); });
  var selectedIds = Object.keys(selected).filter(function (id) { return selected[id]; });
  var allVisible = visibleIds.length > 0 && visibleIds.every(function (id) { return selected[id]; });

  function toggleAll() {
    if (allVisible) {
      var next = Object.assign({}, selected);
      visibleIds.forEach(function (id) { delete next[id]; });
      setSelected(next);
    } else {
      var next = Object.assign({}, selected);
      visibleIds.forEach(function (id) { next[id] = true; });
      setSelected(next);
    }
  }

  function toggleRow(id) {
    setSelected(function (prev) {
      var next = Object.assign({}, prev);
      if (next[id]) delete next[id]; else next[id] = true;
      return next;
    });
  }

  // ── Age display ───────────────────────────────────────────────────────
  function ageLabel(pr) {
    var hours = pr.age_hours != null
      ? pr.age_hours
      : (pr.created_at ? (Date.now() - new Date(pr.created_at).getTime()) / 3600000 : null);
    if (hours == null) return "-";
    if (hours < 48) return hours.toFixed(0) + "h";
    return (hours / 24).toFixed(0) + "d";
  }

  // ── Dispatch helpers ──────────────────────────────────────────────────
  function openDispatchSelected() {
    var items = sortedPRs.filter(function (pr) {
      return selected[String(pr.number || pr.pr_number || pr.id)];
    });
    setDispatchModal({ items: items, mode: "selected" });
    setModalPrompt("");
  }

  function openDispatchAll() {
    if (!window.confirm("Dispatch to all " + sortedPRs.length + " visible PRs?")) return;
    setDispatchModal({ items: sortedPRs, mode: "all" });
    setModalPrompt("");
  }

  function doDispatch() {
    if (!dispatchModal || !dispatchModal.items.length) return;
    setDispatching(true);
    var payload = {
      selection: {
        mode: "list",
        items: dispatchModal.items.map(function (pr) {
          return {
            repo: pr.repo || pr.repository || pr.full_name,
            number: pr.number || pr.pr_number,
            title: pr.title,
          };
        }),
      },
      provider: modalProvider,
      prompt: modalPrompt,
      confirmation: { approved_by: (principal && principal.name) || "anonymous" },
    };
    legacyFetch("/api/prs/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.status); });
        return r.json();
      })
      .then(function () {
        setDispatchMsg({ type: "success", text: "Dispatched " + dispatchModal.items.length + " PR(s) to " + modalProvider });
        setDispatchModal(null);
        setSelected({});
        setTimeout(function () { setDispatchMsg(null); }, 6000);
      })
      .catch(function (err) {
        setDispatchMsg({ type: "error", text: "Dispatch failed: " + err.message });
        setTimeout(function () { setDispatchMsg(null); }, 8000);
      })
      .finally(function () {
        setDispatching(false);
      });
  }

  var PROVIDERS = [
    ["jules_api", "Jules API"],
    ["codex_cli", "Codex CLI"],
    ["claude_code_cli", "Claude Code CLI"],
    ["gemini_cli", "Gemini CLI"],
    ["ollama", "Ollama"],
    ["cline", "Cline"],
  ];

  // ── Render ────────────────────────────────────────────────────────────
  return h(
    "div",
    null,

    // Dispatch status message
    dispatchMsg
      ? h(
          "div",
          {
            role: "alert",
            style: {
              marginBottom: 12,
              padding: "10px 16px",
              borderRadius: 6,
              background: dispatchMsg.type === "error"
                ? "rgba(248,81,73,0.15)"
                : "rgba(63,185,80,0.15)",
              color: dispatchMsg.type === "error"
                ? "var(--accent-red)"
                : "var(--accent-green)",
              border: "1px solid " + (dispatchMsg.type === "error"
                ? "var(--accent-red)"
                : "var(--accent-green)"),
              fontSize: 13,
            },
          },
          dispatchMsg.text,
        )
      : null,

    // ── Filter bar ──────────────────────────────────────────────────────
    h(
      "div",
      {
        style: {
          display: "flex",
          gap: 8,
          marginBottom: 12,
          alignItems: "center",
          flexWrap: "wrap",
        },
      },
      h("input", {
        type: "text",
        placeholder: "Filter by repo (org/repo)…",
        value: repoFilter,
        onChange: function (e) { setRepoFilter(e.target.value); },
        style: {
          flex: "1 1 160px",
          minWidth: 140,
          background: "var(--bg-secondary)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "6px 10px",
          fontSize: 12,
        },
      }),
      h("input", {
        type: "text",
        placeholder: "Filter by author…",
        value: authorFilter,
        onChange: function (e) { setAuthorFilter(e.target.value); },
        style: {
          flex: "1 1 120px",
          minWidth: 100,
          background: "var(--bg-secondary)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "6px 10px",
          fontSize: 12,
        },
      }),
      h(
        "label",
        {
          style: {
            display: "flex",
            alignItems: "center",
            gap: 5,
            fontSize: 12,
            color: "var(--text-secondary)",
            cursor: "pointer",
            whiteSpace: "nowrap",
          },
        },
        h("input", {
          type: "checkbox",
          checked: showDrafts,
          onChange: function (e) { setShowDrafts(e.target.checked); },
        }),
        "Show drafts",
      ),
      h(
        "button",
        {
          className: "btn",
          onClick: fetchPRs,
          disabled: loading,
          style: { marginLeft: "auto", whiteSpace: "nowrap" },
        },
        loading ? h("span", { className: "spinner" }) : I.refresh(12),
        " Refresh",
      ),
    ),

    // ── Table ──────────────────────────────────────────────────────────
    loading && prs.length === 0
      ? h(
          "div",
          {
            style: {
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "24px",
              color: "var(--text-muted)",
              fontSize: 13,
            },
          },
          h("span", { className: "spinner" }),
          "Loading PRs…",
        )
      : fetchError
      ? h(
          "div",
          {
            style: {
              padding: "12px 16px",
              borderRadius: 8,
              background: "rgba(248,81,73,0.12)",
              color: "var(--accent-red)",
              fontSize: 12,
            },
          },
          fetchError,
        )
      : sortedPRs.length === 0
      ? h(
          "div",
          {
            style: {
              textAlign: "center",
              padding: "32px 24px",
              color: "var(--text-muted)",
              fontSize: 13,
            },
          },
          "No open PRs found.",
        )
      : h(
          "div",
          { style: { overflowX: "auto" } },
          h(
            "table",
            { className: "data-table", style: { width: "100%" } },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h(
                  "th",
                  { style: { width: 32, padding: "8px 10px" } },
                  h("input", {
                    type: "checkbox",
                    checked: allVisible,
                    onChange: toggleAll,
                    title: allVisible ? "Deselect all" : "Select all",
                  }),
                ),
                h(SortTh, { label: "Repo", sortKey: "repo", sort: sort, setSort: setSort }),
                h(SortTh, { label: "#", sortKey: "number", sort: sort, setSort: setSort, thProps: { style: { width: 60 } } }),
                h(SortTh, { label: "Title", sortKey: "title", sort: sort, setSort: setSort }),
                h(SortTh, { label: "Author", sortKey: "author", sort: sort, setSort: setSort }),
                h(SortTh, { label: "Age", sortKey: "age", sort: sort, setSort: setSort, thProps: { style: { width: 60 } } }),
                h("th", null, "Draft"),
                h("th", null, "Labels"),
                h("th", null, "Claim"),
              ),
            ),
            h(
              "tbody",
              null,
              sortedPRs.map(function (pr) {
                var id = String(pr.number || pr.pr_number || pr.id);
                var isChecked = !!selected[id];
                var repo = pr.repo || pr.repository || pr.full_name || "-";
                var prNum = pr.number || pr.pr_number || "-";
                var repoUrl = pr.repo_url || pr.repository_url || ("https://github.com/" + repo);
                var prUrl = pr.html_url || pr.url || (repoUrl + "/pull/" + prNum);
                var author = pr.author || (pr.user && pr.user.login) || "-";
                var labels = pr.labels || [];
                var labelNames = labels.map(function (l) { return l.name || l; });
                var shownLabels = labelNames.slice(0, 3);
                var extraLabels = labelNames.length - 3;
                var titleFull = pr.title || "-";
                var titleShort = titleFull.length > 80 ? titleFull.slice(0, 77) + "…" : titleFull;
                var claim = pr.agent_claim || "";

                return h(
                  "tr",
                  {
                    key: id,
                    style: {
                      background: isChecked ? "rgba(88,166,255,0.06)" : undefined,
                    },
                  },
                  h(
                    "td",
                    { style: { width: 32, padding: "8px 10px" } },
                    h("input", {
                      type: "checkbox",
                      checked: isChecked,
                      onChange: function () { toggleRow(id); },
                    }),
                  ),
                  h(
                    "td",
                    null,
                    h(
                      "a",
                      {
                        href: repoUrl,
                        target: "_blank",
                        rel: "noopener noreferrer",
                        style: { color: "var(--accent-blue)", textDecoration: "none", fontSize: 12 },
                      },
                      repo,
                    ),
                  ),
                  h(
                    "td",
                    null,
                    h(
                      "a",
                      {
                        href: prUrl,
                        target: "_blank",
                        rel: "noopener noreferrer",
                        style: { color: "var(--accent-blue)", textDecoration: "none", fontSize: 12 },
                      },
                      "#" + prNum,
                    ),
                  ),
                  h(
                    "td",
                    { title: titleFull, style: { maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
                    titleShort,
                  ),
                  h("td", { style: { fontSize: 12, color: "var(--text-secondary)" } }, author),
                  h("td", { style: { fontSize: 12, whiteSpace: "nowrap" } }, ageLabel(pr)),
                  h(
                    "td",
                    null,
                    pr.draft
                      ? h(
                          "span",
                          {
                            style: {
                              fontSize: 11,
                              padding: "2px 7px",
                              borderRadius: 10,
                              background: "rgba(139,148,158,0.18)",
                              color: "var(--text-muted)",
                              fontWeight: 500,
                            },
                          },
                          "Draft",
                        )
                      : null,
                  ),
                  h(
                    "td",
                    { style: { fontSize: 11 } },
                    shownLabels.map(function (lbl) {
                      return h(
                        "span",
                        {
                          key: lbl,
                          style: {
                            display: "inline-block",
                            marginRight: 3,
                            padding: "2px 6px",
                            borderRadius: 10,
                            background: "rgba(88,166,255,0.12)",
                            color: "var(--accent-blue)",
                            fontSize: 11,
                            fontWeight: 500,
                            whiteSpace: "nowrap",
                          },
                        },
                        lbl,
                      );
                    }),
                    extraLabels > 0
                      ? h(
                          "span",
                          { style: { fontSize: 11, color: "var(--text-muted)", marginLeft: 2 } },
                          "+" + extraLabels,
                        )
                      : null,
                  ),
                  h(
                    "td",
                    { style: { fontSize: 12, color: "var(--text-muted)" } },
                    claim,
                  ),
                );
              }),
            ),
          ),
        ),

    // ── Bulk action bar ────────────────────────────────────────────────
    selectedIds.length > 0
      ? h(
          "div",
          {
            style: {
              marginTop: 12,
              padding: "10px 14px",
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              display: "flex",
              gap: 10,
              alignItems: "center",
              flexWrap: "wrap",
            },
          },
          h(
            "span",
            { style: { fontSize: 12, color: "var(--text-secondary)", marginRight: 4 } },
            selectedIds.length + " PR(s) selected",
          ),
          h(
            "button",
            {
              className: "btn",
              onClick: openDispatchSelected,
            },
            "Dispatch to selected (" + selectedIds.length + ")",
          ),
          h(
            "button",
            {
              className: "btn",
              style: { opacity: 0.8 },
              onClick: openDispatchAll,
            },
            "Dispatch to all (" + sortedPRs.length + ")",
          ),
        )
      : sortedPRs.length > 0
      ? h(
          "div",
          {
            style: {
              marginTop: 10,
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            },
          },
          h(
            "button",
            {
              className: "btn",
              style: { opacity: 0.7 },
              onClick: openDispatchAll,
            },
            "Dispatch to all (" + sortedPRs.length + ")",
          ),
        )
      : null,

    // ── Dispatch modal ─────────────────────────────────────────────────
    dispatchModal
      ? h(
          "div",
          {
            style: {
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.55)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1000,
            },
            onClick: function (e) {
              if (e.target === e.currentTarget) setDispatchModal(null);
            },
            onKeyDown: function (e) {
              if (e.key === "Escape") setDispatchModal(null);
            },
          },
          h(
            "div",
            {
              // a11y (#833): a modal dialog — trap intent declared via
              // aria-modal, labelled by its heading, Escape closes (above).
              role: "dialog",
              "aria-modal": "true",
              "aria-labelledby": "dispatch-modal-title",
              style: {
                background: "var(--bg-primary)",
                border: "1px solid var(--border)",
                borderRadius: 12,
                padding: 24,
                minWidth: 400,
                maxWidth: 560,
                maxHeight: "80vh",
                overflowY: "auto",
              },
            },
            h(
              "div",
              { id: "dispatch-modal-title", style: { fontSize: 15, fontWeight: 600, marginBottom: 12 } },
              "Dispatch to " + dispatchModal.items.length + " PR(s)",
            ),

            // PR list
            h(
              "div",
              {
                style: {
                  maxHeight: 180,
                  overflowY: "auto",
                  marginBottom: 14,
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  background: "var(--bg-secondary)",
                  padding: "6px 10px",
                },
              },
              dispatchModal.items.map(function (pr) {
                var repo = pr.repo || pr.repository || pr.full_name || "-";
                var num = pr.number || pr.pr_number || "-";
                return h(
                  "div",
                  {
                    key: String(num),
                    style: {
                      fontSize: 12,
                      padding: "3px 0",
                      borderBottom: "1px solid var(--border)",
                      color: "var(--text-secondary)",
                    },
                  },
                  repo + " #" + num + " — " + (pr.title || ""),
                );
              }),
            ),

            // Provider selector
            h(
              "label",
              {
                style: {
                  display: "block",
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  marginBottom: 8,
                },
              },
              "Provider",
              h(
                "select",
                {
                  value: modalProvider,
                  onChange: function (e) { setModalProvider(e.target.value); },
                  style: {
                    display: "block",
                    width: "100%",
                    marginTop: 4,
                    background: "var(--bg-secondary)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    padding: "6px 10px",
                    boxSizing: "border-box",
                  },
                },
                PROVIDERS.map(function (entry) {
                  return h("option", { key: entry[0], value: entry[0] }, entry[1]);
                }),
              ),
            ),

            // Prompt textarea
            h(
              "label",
              {
                style: {
                  display: "block",
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  marginBottom: 14,
                },
              },
              "Prompt (optional)",
              h("textarea", {
                value: modalPrompt,
                onChange: function (e) { setModalPrompt(e.target.value); },
                rows: 4,
                placeholder: "Describe what the agent should do with each PR…",
                style: {
                  display: "block",
                  width: "100%",
                  marginTop: 4,
                  background: "var(--bg-secondary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: "6px 10px",
                  fontSize: 12,
                  fontFamily: "inherit",
                  resize: "vertical",
                  boxSizing: "border-box",
                },
              }),
            ),

            // Actions
            h(
              "div",
              { style: { display: "flex", gap: 8 } },
              h(
                "button",
                {
                  className: "btn",
                  onClick: doDispatch,
                  disabled: dispatching,
                },
                dispatching ? h("span", { className: "spinner" }) : null,
                dispatching ? " Dispatching…" : "Confirm dispatch",
              ),
              h(
                "button",
                {
                  className: "btn",
                  style: { opacity: 0.7 },
                  onClick: function () { setDispatchModal(null); },
                  disabled: dispatching,
                },
                "Cancel",
              ),
            ),
          ),
        )
      : null,
  );
}

// ════════════════════════ ISSUES SUB-TAB ════════════════════════
function IssuesSubTab() {
  var issuesState = React.useState([]);
  var issues = issuesState[0], setIssues = issuesState[1];
  var loadingState = React.useState(false);
  var loading = loadingState[0], setLoading = loadingState[1];
  var errorState = React.useState(null);
  var fetchError = errorState[0], setFetchError = errorState[1];
  var sourceState = React.useState(function () { return localStorage.getItem('issuesSourceFilter') || ''; });
  var sourceFilter = sourceState[0], setSourceFilter = sourceState[1];
  var statsState = React.useState(null);
  var issueStats = statsState[0], setIssueStats = statsState[1];
  var sourceOptsState = React.useState([{ value: 'github', label: 'GitHub' }]);
  var sourceOptions = sourceOptsState[0], setSourceOptions = sourceOptsState[1];

  // Filters
  var repoFState = React.useState(function () { return localStorage.getItem('issues:filter_repo') || ''; });
  var repoFilter = repoFState[0], setRepoFilter = repoFState[1];
  var complexFState = React.useState(function () { return localStorage.getItem('issues:filter_complexity') || ''; });
  var complexFilter = complexFState[0], setComplexFilter = complexFState[1];
  var judgeFState = React.useState(function () { return localStorage.getItem('issues:filter_judgement') || ''; });
  var judgeFilter = judgeFState[0], setJudgeFilter = judgeFState[1];
  var pickableFState = React.useState(function () { return localStorage.getItem('issues:filter_pickable') === '1'; });
  var pickableOnly = pickableFState[0], setPickableOnly = pickableFState[1];

  // Selection
  var selectedState = React.useState({});
  var selected = selectedState[0], setSelected = selectedState[1];

  // Dispatch action bar
  var providerState = React.useState('jules_api');
  var dispatchProvider = providerState[0], setDispatchProvider = providerState[1];
  var promptState = React.useState('');
  var dispatchPrompt = promptState[0], setDispatchPrompt = promptState[1];

  // Modal
  var modalState = React.useState(false);
  var showModal = modalState[0], setShowModal = modalState[1];
  var forceState = React.useState(false);
  var forceDispatch = forceState[0], setForceDispatch = forceState[1];
  var dispatchResultState = React.useState(null);
  var dispatchResult = dispatchResultState[0], setDispatchResult = dispatchResultState[1];

  function issueKey(issue) {
    return [
      issue.repo || issue.repository || '',
      issue.number != null ? String(issue.number) : ((issue.linear && issue.linear.id) || issue.url || issue.title || 'linear'),
    ].join(':');
  }

  function fetchIssues() {
    var activeSource = sourceFilter || 'github';
    setLoading(true);
    setFetchError(null);
    legacyFetch('/api/issues?limit=2000&source=' + encodeURIComponent(activeSource))
      .then(function (r) {
        if (!r.ok) { throw new Error('HTTP ' + r.status); }
        return r.json();
      })
      .then(function (data) {
        setIssues(Array.isArray(data) ? data : (data.items || data.issues || []));
        setIssueStats((data && data.stats) || null);
        setLoading(false);
      })
      .catch(function (err) {
        setFetchError(err.message || 'Failed to load issues');
        setIssueStats(null);
        setLoading(false);
      });
  }

  React.useEffect(function () {
    legacyFetch('/api/linear/workspaces')
      .then(function (r) {
        if (!r.ok) { throw new Error('HTTP ' + r.status); }
        return r.json();
      })
      .then(function (data) {
        var workspaces = (data && data.workspaces) || [];
        var linearReady = workspaces.some(function (workspace) {
          return workspace && workspace.auth_status === 'ok';
        });
        var nextOptions = linearReady
          ? [
              { value: 'github', label: 'GitHub' },
              { value: 'linear', label: 'Linear' },
              { value: 'unified', label: 'Unified' }
            ]
          : [{ value: 'github', label: 'GitHub' }];
        var stored = localStorage.getItem('issuesSourceFilter') || '';
        setSourceOptions(nextOptions);
        setSourceFilter(function (current) {
          if (current && nextOptions.some(function (option) { return option.value === current; })) {
            return current;
          }
          if (stored && nextOptions.some(function (option) { return option.value === stored; })) {
            return stored;
          }
          return linearReady ? 'unified' : 'github';
        });
      })
      .catch(function () {
        setSourceOptions([{ value: 'github', label: 'GitHub' }]);
        setSourceFilter(function (current) { return current || 'github'; });
      });
  }, []);

  React.useEffect(function () { if (sourceFilter) { fetchIssues(); } }, [sourceFilter]);

  // Persist filters
  React.useEffect(function () { if (sourceFilter) { localStorage.setItem('issuesSourceFilter', sourceFilter); } }, [sourceFilter]);
  React.useEffect(function () { localStorage.setItem('issues:filter_repo', repoFilter); }, [repoFilter]);
  React.useEffect(function () { localStorage.setItem('issues:filter_complexity', complexFilter); }, [complexFilter]);
  React.useEffect(function () { localStorage.setItem('issues:filter_judgement', judgeFilter); }, [judgeFilter]);
  React.useEffect(function () { localStorage.setItem('issues:filter_pickable', pickableOnly ? '1' : '0'); }, [pickableOnly]);

  var repos = Array.from(new Set(issues.map(function (i) { return i.repo || i.repository || ''; }).filter(Boolean))).sort();

  var filtered = issues.filter(function (issue) {
    var taxonomy = issue.taxonomy || {};
    var repo = issue.repo || issue.repository || '';
    if (repoFilter && repo !== repoFilter) return false;
    if (complexFilter && taxonomy.complexity !== complexFilter) return false;
    if (judgeFilter && taxonomy.judgement !== judgeFilter) return false;
    if (pickableOnly && !issue.pickable) return false;
    return true;
  });

  var selectedItems = filtered.filter(function (issue) {
    return selected[issueKey(issue)];
  });
  var selectedCount = selectedItems.length;
  var hasNonPickable = selectedItems.some(function (i) { return !i.pickable; });
  var hasDangerous = selectedItems.some(function (i) {
    var j = (i.taxonomy || {}).judgement;
    return j === 'design' || j === 'contested';
  });

  function toggleSelect(issue) {
    var key = issueKey(issue);
    setSelected(function (prev) {
      var next = Object.assign({}, prev);
      if (next[key]) { delete next[key]; } else { next[key] = true; }
      return next;
    });
  }

  function toggleAll(checked) {
    if (!checked) { setSelected({}); return; }
    var next = {};
    filtered.forEach(function (issue) {
      var repo = issue.repo || issue.repository || '';
      if (issue.pickable !== false && repo && issue.number != null) {
        next[issueKey(issue)] = true;
      }
    });
    setSelected(next);
  }

  // Issue #826: issue-type / complexity / judgement badge colours are driven
  // by the semantic --badge-* / --accent-* design tokens (defined per-theme in
  // index.css) so they re-tint under non-default themes instead of being frozen
  // to hardcoded hex. The CSS vars carry sensible fallbacks for environments
  // where the stylesheet has not yet loaded.
  function getTypeStyle(type) {
    var map = {
      epic: { background: 'var(--badge-neutral-bg)', color: 'var(--badge-neutral-fg)' },
      task: { background: 'var(--badge-info-bg)', color: 'var(--badge-info-fg)' },
      bug: { background: 'var(--badge-danger-bg)', color: 'var(--badge-danger-fg)' },
      security: { background: 'var(--badge-danger-bg)', color: 'var(--badge-danger-fg)' },
      research: { background: 'var(--badge-purple-bg)', color: 'var(--accent-purple)' },
      docs: { background: 'var(--badge-info-bg)', color: 'var(--accent-blue)' },
      chore: { background: 'var(--badge-neutral-bg)', color: 'var(--badge-neutral-fg)' },
    };
    return map[type] || { background: 'var(--badge-neutral-bg)', color: 'var(--badge-neutral-fg)' };
  }

  function getComplexityStyle(complexity) {
    var map = {
      trivial: { background: 'var(--badge-success-bg)', color: 'var(--badge-success-fg)' },
      routine: { background: 'var(--badge-info-bg)', color: 'var(--badge-info-fg)' },
      complex: { background: 'var(--badge-warning-bg)', color: 'var(--badge-warning-fg)' },
      deep: { background: 'var(--badge-danger-bg)', color: 'var(--badge-danger-fg)' },
      research: { background: 'var(--badge-purple-bg)', color: 'var(--accent-purple)' },
    };
    return map[complexity] || { background: 'var(--badge-neutral-bg)', color: 'var(--badge-neutral-fg)' };
  }

  function getJudgementStyle(judgement) {
    if (judgement === 'design' || judgement === 'contested') {
      return { background: 'var(--badge-danger-bg)', color: 'var(--badge-danger-fg)' };
    }
    var map = {
      objective: { background: 'var(--badge-success-bg)', color: 'var(--badge-success-fg)' },
      preference: { background: 'var(--badge-warning-bg)', color: 'var(--badge-warning-fg)' },
    };
    return map[judgement] || { background: 'var(--badge-neutral-bg)', color: 'var(--badge-neutral-fg)' };
  }

  function pillStyle(style) {
    return Object.assign({
      display: 'inline-block',
      padding: '1px 7px',
      borderRadius: 10,
      fontSize: 11,
      fontWeight: 600,
      whiteSpace: 'nowrap',
    }, style);
  }

  function doDispatch() {
    var items = selectedItems.map(function (i) {
      return { repo: i.repo || i.repository || '', number: i.number };
    });
    setDispatchResult(null);
    legacyFetch('/api/issues/dispatch', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify({
        selection: { mode: 'list', items: items },
        provider: dispatchProvider,
        prompt: dispatchPrompt,
        force: forceDispatch,
        confirmation: { approved_by: (principal && principal.name) || 'anonymous' },
      }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (result) {
        if (result.ok) {
          setDispatchResult({ type: 'success', text: 'Dispatched ' + items.length + ' issue(s) successfully.' });
          setSelected({});
        } else {
          setDispatchResult({ type: 'error', text: 'Dispatch failed: ' + (result.data.detail || JSON.stringify(result.data)) });
        }
        setShowModal(false);
        setForceDispatch(false);
      })
      .catch(function (err) {
        setDispatchResult({ type: 'error', text: 'Dispatch error: ' + err.message });
        setShowModal(false);
      });
  }

  var providerOptions = ['jules_api', 'codex_cli', 'claude_code_cli', 'gemini_cli', 'ollama', 'cline'];

  return h('div', { style: { padding: '0 0 16px 0' } },
    // Filter bar
    h('div', {
      style: {
        display: 'flex',
        gap: 8,
        alignItems: 'center',
        flexWrap: 'wrap',
        marginBottom: 12,
        padding: '10px 12px',
        background: 'var(--bg-secondary)',
        borderRadius: 8,
        border: '1px solid var(--border)',
      }
    },
        h('select', {
          value: sourceFilter || 'github',
          onChange: function (e) { setSourceFilter(e.target.value); setSelected({}); },
          style: { fontSize: 12, padding: '3px 6px', background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 4 },
        },
          sourceOptions.map(function (option) {
            return h('option', { key: option.value, value: option.value }, option.label);
          })
        ),
      h('select', {
        value: repoFilter,
        onChange: function (e) { setRepoFilter(e.target.value); setSelected({}); },
        style: { fontSize: 12, padding: '3px 6px', background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 4 },
      },
        h('option', { value: '' }, 'All repos'),
        repos.map(function (r) { return h('option', { key: r, value: r }, r); })
      ),
      h('select', {
        value: complexFilter,
        onChange: function (e) { setComplexFilter(e.target.value); setSelected({}); },
        style: { fontSize: 12, padding: '3px 6px', background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 4 },
      },
        h('option', { value: '' }, 'All complexity'),
        ['trivial', 'routine', 'complex', 'deep', 'research'].map(function (c) { return h('option', { key: c, value: c }, c); })
      ),
      h('select', {
        value: judgeFilter,
        onChange: function (e) { setJudgeFilter(e.target.value); setSelected({}); },
        style: { fontSize: 12, padding: '3px 6px', background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 4 },
      },
        h('option', { value: '' }, 'All judgement'),
        ['objective', 'preference', 'design', 'contested'].map(function (j) { return h('option', { key: j, value: j }, j); })
      ),
      h('label', { style: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, cursor: 'pointer', userSelect: 'none' } },
        h('input', {
          type: 'checkbox',
          checked: pickableOnly,
          onChange: function (e) { setPickableOnly(e.target.checked); setSelected({}); },
        }),
        'Pickable only'
      ),
      h('button', {
        className: 'btn',
        onClick: fetchIssues,
        style: { marginLeft: 'auto' },
      }, I.refresh(12), 'Refresh'),
    ),

    // Dispatch result banner
    dispatchResult ? h('div', {
      style: {
        marginBottom: 10,
        padding: '8px 12px',
        borderRadius: 6,
        fontSize: 12,
        background: dispatchResult.type === 'success' ? 'rgba(63,185,80,0.12)' : 'rgba(248,81,73,0.12)',
        color: dispatchResult.type === 'success' ? 'var(--accent-green)' : 'var(--accent-red)',
      }
    }, dispatchResult.text) : null,

    sourceFilter === 'unified' && issueStats ? h('div', {
      style: {
        marginBottom: 10,
        padding: '8px 12px',
        borderRadius: 6,
        fontSize: 12,
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border)',
        color: 'var(--text-secondary)',
      }
    },
      (issueStats.unified_total || filtered.length) + ' issues - ' +
      (issueStats.github_total || 0) + ' GitHub, ' +
      (issueStats.linear_total || 0) + ' Linear, ' +
      (issueStats.collapsed || 0) + ' collapsed'
    ) : null,

    // Loading / error
    loading ? h('div', { style: { color: 'var(--text-muted)', fontSize: 12, padding: '12px 0' } }, 'Loading issues...') : null,
    fetchError ? h('div', {
      style: {
        padding: '10px 12px', borderRadius: 8,
        background: 'rgba(248,81,73,0.12)', color: 'var(--accent-red)', fontSize: 12,
      }
    }, fetchError) : null,

    // Table
    !loading && !fetchError ? h('div', { style: { overflowX: 'auto' } },
      filtered.length === 0
        ? h('div', { style: { color: 'var(--text-muted)', fontSize: 13, padding: '24px 0', textAlign: 'center' } }, 'No issues match the current filters.')
        : h('table', {
            style: {
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 12,
            }
          },
          h('thead', null,
            h('tr', { style: { borderBottom: '1px solid var(--border)' } },
              h('th', { style: { padding: '6px 8px', textAlign: 'center', width: 28 } },
                h('input', {
                  type: 'checkbox',
                  onChange: function (e) { toggleAll(e.target.checked); },
                  checked: filtered.length > 0 && filtered.filter(function (i) { return i.pickable !== false; }).every(function (i) {
                    var repo = i.repo || i.repository || '';
                    return (!repo || i.number == null) ? true : selected[issueKey(i)];
                  }),
                })
              ),
              h('th', { style: { padding: '6px 8px', textAlign: 'left' } }, 'Repo'),
              h('th', { style: { padding: '6px 8px', textAlign: 'left' } }, '#'),
              h('th', { style: { padding: '6px 8px', textAlign: 'left' } }, 'Title'),
              h('th', { style: { padding: '6px 8px', textAlign: 'left' } }, 'Type'),
              h('th', { style: { padding: '6px 8px', textAlign: 'left' } }, 'Complexity'),
              h('th', { style: { padding: '6px 8px', textAlign: 'left' } }, 'Effort'),
              h('th', { style: { padding: '6px 8px', textAlign: 'left' } }, 'Judgement'),
              h('th', { style: { padding: '6px 8px', textAlign: 'center' } }, 'Pickable'),
            )
          ),
          h('tbody', null,
            filtered.map(function (issue) {
              var taxonomy = issue.taxonomy || {};
              var repo = issue.repo || issue.repository || '';
              var key = issueKey(issue);
              var isSelected = !!selected[key];
              var pickable = issue.pickable !== false;
              var dispatchable = !!repo && issue.number != null;
              var selectable = pickable && dispatchable;
              var blockedBy = issue.pickable_blocked_by || [];
              var title = (issue.title || '');
              var truncTitle = title.length > 80 ? title.slice(0, 80) + '…' : title;
              var issueUrl = issue.url || issue.html_url || (repo && issue.number != null ? ('https://github.com/' + repo + '/issues/' + issue.number) : '#');
              var repoUrl = 'https://github.com/' + repo;
              var typeStyle = getTypeStyle(taxonomy.type || taxonomy.issue_type);
              var complexityStyle = getComplexityStyle(taxonomy.complexity);
              var judgementStyle = getJudgementStyle(taxonomy.judgement);
              var isDangerous = taxonomy.judgement === 'design' || taxonomy.judgement === 'contested';
              var sources = Array.isArray(issue.sources) && issue.sources.length ? issue.sources : ['github'];
              var linearId = issue.linear && issue.linear.identifier ? issue.linear.identifier : '';
              var linearUrl = issue.linear && issue.linear.url ? issue.linear.url : '';
              return h('tr', {
                key: key,
                style: {
                  borderBottom: '1px solid var(--border)',
                  opacity: selectable ? 1 : 0.7,
                  background: selectable ? 'transparent' : 'rgba(255,0,0,0.04)',
                }
              },
                h('td', { style: { padding: '6px 8px', textAlign: 'center' } },
                  h('input', {
                    type: 'checkbox',
                    checked: isSelected,
                    disabled: !selectable,
                    title: !dispatchable ? 'Linear-only items cannot be dispatched until linked to a GitHub issue.' : (!pickable && blockedBy.length ? blockedBy.join(', ') : undefined),
                    style: selectable ? {} : { cursor: 'not-allowed', opacity: 0.5 },
                    onChange: function () { if (selectable) toggleSelect(issue); },
                  })
                ),
                h('td', { style: { padding: '6px 8px', whiteSpace: 'nowrap' } },
                  repo
                    ? h('a', { href: repoUrl, target: '_blank', rel: 'noreferrer', style: { color: 'var(--text-secondary)', textDecoration: 'none' } }, repo)
                    : h('span', { style: { color: 'var(--text-muted)' } }, linearId || 'Linear-only')
                ),
                h('td', { style: { padding: '6px 8px', whiteSpace: 'nowrap' } },
                  issue.number != null
                    ? h('a', { href: issueUrl, target: '_blank', rel: 'noreferrer', style: { color: 'var(--accent-blue)', textDecoration: 'none' } }, '#' + issue.number)
                    : h('a', { href: linearUrl || issueUrl, target: '_blank', rel: 'noreferrer', style: { color: 'var(--accent-blue)', textDecoration: 'none' } }, linearId || 'Linear')
                ),
                h('td', { style: { padding: '6px 8px', maxWidth: 300 } },
                  h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 4 } },
                    sources.map(function (src) {
                      return h('span', {
                        key: src,
                        style: pillStyle(src === 'linear' ? { background: 'rgba(99,102,241,0.18)', color: 'var(--accent-purple)' } : { background: 'rgba(88,166,255,0.18)', color: 'var(--accent-blue)' }),
                      }, src.toUpperCase());
                    }),
                    linearId
                      ? h('a', {
                          href: linearUrl || issueUrl,
                          target: '_blank',
                          rel: 'noreferrer',
                          style: { color: 'var(--accent-purple)', textDecoration: 'none', fontWeight: 600 },
                        }, linearId)
                      : null
                  ),
                  taxonomy.quick_win ? h('span', { style: { color: 'var(--accent-yellow)', marginRight: 4 } }, '★') : null,
                  h('span', { title: title }, truncTitle)
                ),
                h('td', { style: { padding: '6px 8px' } },
                  taxonomy.type || taxonomy.issue_type
                    ? h('span', { style: pillStyle(typeStyle) }, taxonomy.type || taxonomy.issue_type)
                    : h('span', { style: { color: 'var(--text-muted)' } }, '—')
                ),
                h('td', { style: { padding: '6px 8px' } },
                  taxonomy.complexity
                    ? h('span', { style: pillStyle(complexityStyle) }, taxonomy.complexity)
                    : h('span', { style: { color: 'var(--text-muted)' } }, '—')
                ),
                h('td', { style: { padding: '6px 8px', whiteSpace: 'nowrap' } }, taxonomy.effort || '—'),
                h('td', { style: { padding: '6px 8px' } },
                  taxonomy.judgement
                    ? h('span', { style: pillStyle(judgementStyle) },
                        isDangerous ? '🛑 ' : '',
                        taxonomy.judgement
                      )
                    : h('span', { style: { color: 'var(--text-muted)' } }, '—')
                ),
                h('td', { style: { padding: '6px 8px', textAlign: 'center' } },
                  selectable
                    ? h('span', { style: { color: 'var(--accent-green)', fontSize: 14 } }, '✓')
                    : h('span', { title: !dispatchable ? 'Linear-only items cannot be dispatched until linked to a GitHub issue.' : blockedBy.join(', '), style: { color: 'var(--accent-red)', fontSize: 14, cursor: 'help' } }, '✗')
                )
              );
            })
          )
        )
    ) : null,

    // Action bar
    selectedCount > 0 ? h('div', {
      style: {
        marginTop: 16,
        padding: '12px 16px',
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        display: 'flex',
        gap: 12,
        alignItems: 'flex-start',
        flexWrap: 'wrap',
      }
    },
      h('div', { style: { fontSize: 12, color: 'var(--text-secondary)', alignSelf: 'center' } },
        selectedCount + ' issue' + (selectedCount !== 1 ? 's' : '') + ' selected'
      ),
      h('select', {
        value: dispatchProvider,
        onChange: function (e) { setDispatchProvider(e.target.value); },
        style: { fontSize: 12, padding: '4px 8px', background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 4 },
      },
        providerOptions.map(function (p) { return h('option', { key: p, value: p }, p); })
      ),
      h('textarea', {
        value: dispatchPrompt,
        onChange: function (e) { setDispatchPrompt(e.target.value); },
        placeholder: 'Optional prompt / instructions for agent…',
        rows: 2,
        style: {
          flex: '1 1 200px',
          fontSize: 12,
          padding: '4px 8px',
          background: 'var(--bg-input)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border)',
          borderRadius: 4,
          resize: 'vertical',
          minWidth: 150,
        },
      }),
      h('button', {
        className: 'btn btn-primary',
        onClick: function () { setShowModal(true); setForceDispatch(false); },
      }, 'Dispatch to selected'),
    ) : null,

    // Confirmation modal
    showModal ? h('div', {
      style: {
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      },
      onClick: function (e) { if (e.target === e.currentTarget) { setShowModal(false); } },
      onKeyDown: function (e) { if (e.key === 'Escape') { setShowModal(false); } },
    },
      h('div', {
        // a11y (#833): confirm-dispatch dialog.
        role: 'dialog',
        'aria-modal': 'true',
        'aria-labelledby': 'confirm-dispatch-title',
        style: {
          background: 'var(--bg-primary)',
          border: '1px solid var(--border)',
          borderRadius: 10,
          padding: 24,
          maxWidth: 540,
          width: '90vw',
          maxHeight: '80vh',
          overflowY: 'auto',
        }
      },
        h('h3', { id: 'confirm-dispatch-title', style: { margin: '0 0 12px 0', fontSize: 15 } }, 'Confirm Dispatch'),

        hasDangerous ? h('div', {
          style: {
            marginBottom: 12,
            padding: '8px 12px',
            borderRadius: 6,
            background: 'rgba(220,38,38,0.15)',
            color: 'var(--accent-red)',
            fontSize: 12,
            fontWeight: 600,
          }
        }, '🛑 Warning: one or more selected issues have judgement:design or judgement:contested. These require panel review and should not be auto-dispatched.') : null,

        h('div', { style: { fontSize: 12, marginBottom: 10, color: 'var(--text-secondary)' } },
          'Dispatching ' + selectedCount + ' issue' + (selectedCount !== 1 ? 's' : '') + ' to provider: ',
          h('strong', null, dispatchProvider)
        ),

        h('div', { style: { maxHeight: 200, overflowY: 'auto', marginBottom: 12 } },
          selectedItems.map(function (issue) {
            var repo = issue.repo || issue.repository || '';
            return h('div', {
              key: issue.number + ':' + repo,
              style: {
                padding: '4px 8px',
                fontSize: 12,
                borderBottom: '1px solid var(--border)',
                opacity: issue.pickable === false ? 0.6 : 1,
              }
            },
              h('span', { style: { color: 'var(--text-muted)' } }, repo + ' '),
              h('strong', null, '#' + issue.number),
              ' — ',
              h('span', null, (issue.title || '').slice(0, 80)),
              issue.pickable === false ? h('span', { style: { color: 'var(--accent-red)', marginLeft: 6 } }, '(non-pickable)') : null
            );
          })
        ),

        hasNonPickable ? h('label', {
          style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginBottom: 12, cursor: 'pointer' }
        },
          h('input', {
            type: 'checkbox',
            checked: forceDispatch,
            onChange: function (e) { setForceDispatch(e.target.checked); },
          }),
          h('span', { style: { color: 'var(--accent-red)', fontWeight: 600 } }, 'Force dispatch (include non-pickable issues)')
        ) : null,

        h('div', { style: { display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 } },
          h('button', {
            className: 'btn',
            onClick: function () { setShowModal(false); },
          }, 'Cancel'),
          h('button', {
            className: 'btn btn-primary',
            onClick: doDispatch,
          }, 'Confirm Dispatch'),
        )
      )
    ) : null,
  );
}

// ════════════════════════ MAIN APP ════════════════════════
var _PROVIDER_MODELS = {
  claude_code_cli: [
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6" },
    { value: "claude-opus-4-6", label: "Opus 4.6" },
    { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
  ],
  codex_cli: [
    { value: "o4-mini", label: "o4-mini" },
    { value: "o3", label: "o3" },
    { value: "gpt-4o", label: "GPT-4o" },
  ],
  gemini_cli: [
    { value: "gemini-2.5-flash", label: "2.5 Flash" },
    { value: "gemini-2.5-pro", label: "2.5 Pro" },
    { value: "gemini-2.0-flash", label: "2.0 Flash" },
  ],
  jules_api: [
    { value: "gemini-2.5-pro", label: "2.5 Pro" },
  ],
};

function RemediationTab(p) {
  var config = p.config || {};
  var workflows = p.workflows || {};
  var runs = p.runs || [];
  var loading = p.loading;
  var error = p.error;
  var selectedRunId = p.selectedRunId;
  var setSelectedRunId = p.setSelectedRunId;
  var provider = p.provider;
  var setProvider = p.setProvider;
  var model = p.model || "";
  var setModel = p.setModel || function() {};
  var plan = p.plan;
  var dispatchState = p.dispatchState;
  var onRefresh = p.onRefresh;
  var onSaveConfig = p.onSaveConfig;
  var onPreview = p.onPreview;
  var onDispatch = p.onDispatch;
  var history = p.history || [];
  var failedRuns = runs.filter(function (run) {
    return run.conclusion === "failure";
  });
  var selectedRun =
    failedRuns.find(function (run) {
      return String(run.id) === String(selectedRunId);
    }) ||
    failedRuns[0] ||
    null;
  var policy = config.policy || {};
  var providers = config.providers || {};
  var availability = config.availability || {};
  var providerOrder = [
    "jules_api",
    "codex_cli",
    "claude_code_cli",
    "gemini_cli",
    "ollama",
    "cline",
  ];
  var providerEntries = Object.keys(providers).length
    ? Object.keys(providers).map(function (providerId) {
        return [providerId, providers[providerId]];
      })
    : providerOrder.map(function (providerId) {
        return [providerId, { label: providerId, notes: "" }];
      });
  var drr = React.useState(policy.workflow_type_rules || {});
  var draftRules = drr[0],
    setDraftRules = drr[1];
  var sps = React.useState(false);
  var savingPolicy = sps[0],
    setSavingPolicy = sps[1];
  var lge = React.useState(false);
  var editingLoopGuard = lge[0],
    setEditingLoopGuard = lge[1];
  var lgv = React.useState(
    policy.max_same_failure_attempts != null
      ? String(policy.max_same_failure_attempts)
      : "3",
  );
  var loopGuardValue = lgv[0],
    setLoopGuardValue = lgv[1];
  var dpe = React.useState(false);
  var editingDefaultProvider = dpe[0],
    setEditingDefaultProvider = dpe[1];
  // Inline status for Jules dispatch – replaces alert() (issue #51)
  var jdm = React.useState(null);
  var julesDispatchMsg = jdm[0],
    setJulesDispatchMsg = jdm[1];
  var mrs = React.useState(null);
  var mobileRemediationSheetRun = mrs[0],
    setMobileRemediationSheetRun = mrs[1];
  var mrp = React.useState(false);
  var mobileRemediationPickerOpen = mrp[0],
    setMobileRemediationPickerOpen = mrp[1];
  React.useEffect(
    function () {
      setDraftRules(
        (config.policy && config.policy.workflow_type_rules) || {},
      );
      setLoopGuardValue(
        config.policy && config.policy.max_same_failure_attempts != null
          ? String(config.policy.max_same_failure_attempts)
          : "3",
      );
    },
    [config],
  );
  function updateRule(workflowType, fieldName, value) {
    setDraftRules(function (prev) {
      var next = Object.assign({}, prev);
      next[workflowType] = Object.assign({}, prev[workflowType] || {}, {
        [fieldName]: value,
      });
      return next;
    });
  }
  function savePolicy(extraFields) {
    setSavingPolicy(true);
    Promise.resolve(
      onSaveConfig(
        Object.assign({}, policy, extraFields || {}, {
          workflow_type_rules: draftRules,
        }),
      ),
    ).finally(function () {
      setSavingPolicy(false);
    });
  }
  function saveLoopGuard() {
    var v = parseInt(loopGuardValue, 10);
    if (!isNaN(v) && v > 0) {
      savePolicy({ max_same_failure_attempts: v });
    }
    setEditingLoopGuard(false);
  }
  function saveDefaultProvider(val) {
    savePolicy({ default_provider: val });
    setEditingDefaultProvider(false);
  }
  function providerLabel(providerId) {
    var entry = providerEntries.find(function (providerEntry) {
      return providerEntry[0] === providerId;
    });
    return (entry && entry[1] && entry[1].label) || providerId;
  }
  function recommendedProviderId() {
    return (
      (plan && plan.decision && plan.decision.provider_id) ||
      provider ||
      policy.default_provider ||
      "jules_api"
    );
  }
  function remediationRunTitle(run) {
    if (!run) return "Failed run";
    var repoName =
      run.repository && run.repository.name
        ? run.repository.name
        : "repo";
    return (
      repoName +
      " / " +
      (run.name || run.workflow_name || "workflow") +
      " #" +
      run.id
    );
  }
  function openMobileRemediationSheet(run) {
    setSelectedRunId(String(run.id));
    setMobileRemediationPickerOpen(false);
    setMobileRemediationSheetRun(run);
  }
  function dispatchFromMobileSheet(run) {
    setSelectedRunId(String(run.id));
    onDispatch(run);
    setMobileRemediationSheetRun(null);
  }
  var accepted = !!(plan && plan.decision && plan.decision.accepted);
  var sta = React.useState(
    (function () {
      try { return localStorage.getItem("remediation-subtab") || "automations"; } catch (e) { return "automations"; }
    })()
  );
  var subTab = sta[0],
    setSubTab = sta[1];

  return h(
    "div",
    null,
    h(SubTabs, {
      tabs: [
        { key: "automations", label: "Automations" },
        { key: "prs", label: "PRs" },
        { key: "issues", label: "Issues" },
      ],
      activeKey: subTab,
      onChange: setSubTab,
      storageKey: "remediation-subtab",
      className: "remediation-mobile-tabs",
    }),
    dispatchState
      ? h(
          "div",
          {
            className: "remediation-inflight-tile",
            role: "status",
            "aria-live": "polite",
          },
          h(
            "div",
            {
              style: {
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 10,
                flexWrap: "wrap",
              },
            },
            h(
              "strong",
              { style: { fontSize: 13 } },
              dispatchState.error ? "Dispatch needs attention" : "Agent working",
            ),
            h(
              "span",
              { className: "section-badge" },
              dispatchState.error ? "error" : "in flight",
            ),
          ),
          h(
            "div",
            {
              style: {
                marginTop: 6,
                fontSize: 12,
                color: dispatchState.error
                  ? "var(--accent-red)"
                  : "var(--text-secondary)",
              },
            },
            dispatchState.error ||
              dispatchState.note ||
              "Dispatch submitted. Waiting for the next history refresh.",
          ),
        )
      : null,
    mobileRemediationSheetRun
      ? (function () {
          var sheetRun = mobileRemediationSheetRun;
          var repoName =
            sheetRun.repository && sheetRun.repository.name
              ? sheetRun.repository.name
              : "repo";
          var branch = sheetRun.head_branch || "branch";
          var ghUrl =
            sheetRun.html_url ||
            "https://github.com/D-sorganization/" +
              repoName +
              "/actions/runs/" +
              sheetRun.id;
          var recommendedId = recommendedProviderId();
          return h(
            "div",
            {
              className: "mobile-remediation-sheet",
              role: "dialog",
              "aria-modal": "true",
              "aria-label": "Mobile remediation dispatch",
              onClick: function (e) {
                if (e.target === e.currentTarget) {
                  setMobileRemediationSheetRun(null);
                }
              },
            },
            h(
              "div",
              { className: "mobile-remediation-sheet-panel" },
              h(
                "div",
                {
                  style: {
                    display: "flex",
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                    gap: 12,
                    marginBottom: 12,
                  },
                },
                h(
                  "div",
                  { style: { minWidth: 0 } },
                  h(
                    "div",
                    {
                      style: {
                        fontSize: 14,
                        fontWeight: 700,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      },
                    },
                    remediationRunTitle(sheetRun),
                  ),
                  h(
                    "div",
                    {
                      style: {
                        marginTop: 3,
                        fontSize: 12,
                        color: "var(--text-muted)",
                      },
                    },
                    "Branch " + branch + " | recommended " + providerLabel(recommendedId),
                  ),
                ),
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function () {
                      setMobileRemediationSheetRun(null);
                    },
                  },
                  "Close",
                ),
              ),
              h(
                "div",
                {
                  style: {
                    display: "grid",
                    gap: 8,
                  },
                },
                h(
                  "button",
                  {
                    className: "btn btn-primary",
                    disabled: loading,
                    onClick: function () {
                      dispatchFromMobileSheet(sheetRun);
                    },
                    style: {
                      justifyContent: "center",
                      padding: "10px 12px",
                      fontSize: 13,
                    },
                  },
                  "Dispatch " + providerLabel(recommendedId),
                ),
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function () {
                      setMobileRemediationPickerOpen(!mobileRemediationPickerOpen);
                    },
                    style: { justifyContent: "center" },
                  },
                  mobileRemediationPickerOpen ? "Hide agent picker" : "Pick agent...",
                ),
                mobileRemediationPickerOpen
                  ? h(
                      "select",
                      {
                        value: provider,
                        onChange: function (e) {
                          setProvider(e.target.value);
                        },
                        style: {
                          width: "100%",
                          background: "var(--bg-secondary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border)",
                          borderRadius: 6,
                          padding: "9px 10px",
                        },
                      },
                      providerEntries.map(function (entry) {
                        return h(
                          "option",
                          { key: "mobile-agent-" + entry[0], value: entry[0] },
                          entry[1].label || entry[0],
                        );
                      }),
                    )
                  : null,
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function () {
                      onPreview(sheetRun);
                      setMobileRemediationSheetRun(null);
                    },
                    style: { justifyContent: "center" },
                  },
                  "Preview safety plan",
                ),
                h(
                  "a",
                  {
                    className: "btn",
                    href: ghUrl,
                    target: "_blank",
                    rel: "noopener noreferrer",
                    style: {
                      justifyContent: "center",
                      textDecoration: "none",
                    },
                  },
                  "Open on desktop",
                ),
              ),
            ),
          );
        })()
      : null,
    subTab === "automations" && h(
      "div",
      null,
    // ── Manual Dispatch section (TOP) ────────────────────────────────
    h(
      "div",
      { className: "section", style: { marginBottom: 16 } },
      h(
        "div",
        { className: "section-header" },
        h(
          "span",
          { className: "section-title" },
          I.issue(14),
          "Manual Dispatch",
        ),
        h(
          "button",
          { className: "btn", onClick: onRefresh },
          I.refresh(12),
          "Refresh",
        ),
      ),
      h(
        "div",
        { className: "section-body" },
        error
          ? h(
              "div",
              {
                style: {
                  marginBottom: 12,
                  padding: "10px 12px",
                  borderRadius: 8,
                  background: "rgba(248,81,73,0.12)",
                  color: "var(--accent-red)",
                  fontSize: 12,
                },
              },
              error,
            )
          : null,
        failedRuns.length === 0
          ? h(
              "div",
              {
                style: {
                  color: "var(--text-muted)",
                  fontSize: 12,
                  padding: "8px 0",
                },
              },
              "No failed runs in the current dashboard sample.",
            )
          : failedRuns.map(function (run) {
              var isSelected =
                String(run.id) === String(selectedRunId) ||
                (!selectedRunId &&
                  selectedRun &&
                  String(run.id) === String(selectedRun.id));
              var repoName =
                run.repository && run.repository.name
                  ? run.repository.name
                  : "repo";
              var workflowName =
                run.name || run.workflow_name || "workflow";
              var branch = run.head_branch || "branch";
              var ghUrl =
                run.html_url ||
                "https://github.com/D-sorganization/" +
                  repoName +
                  "/actions/runs/" +
                  run.id;
              return h(
                "div",
                {
                  key: run.id,
                  onClick: function () {
                    openMobileRemediationSheet(run);
                  },
                  style: {
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "10px 12px",
                    marginBottom: 6,
                    borderRadius: 8,
                    border:
                      "1px solid " +
                      (isSelected
                        ? "var(--accent-green)"
                        : "var(--border)"),
                    background: isSelected
                      ? "rgba(63,185,80,0.07)"
                      : "var(--bg-secondary)",
                    cursor: "pointer",
                  },
                },
                h(
                  "div",
                  { style: { flex: 1, minWidth: 0 } },
                  h(
                    "div",
                    {
                      style: {
                        fontSize: 13,
                        fontWeight: 600,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      },
                    },
                    repoName +
                      " \xB7 " +
                      workflowName +
                      " \xB7 " +
                      branch +
                      " #" +
                      run.id,
                  ),
                  h(
                    "div",
                    {
                      style: {
                        fontSize: 11,
                        color: "var(--text-muted)",
                        marginTop: 2,
                      },
                    },
                    run.created_at
                      ? run.created_at.replace("T", " ").slice(0, 19) +
                          " UTC"
                      : "",
                  ),
                ),
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      gap: 4,
                      flexWrap: "wrap",
                      justifyContent: "flex-end",
                    },
                  },
                  [
                    {
                      label: "Run",
                      href: ghUrl,
                    },
                    {
                      label: "Repo",
                      href:
                        run.repository &&
                        run.repository.html_url
                          ? run.repository.html_url
                          : "https://github.com/D-sorganization/" + repoName,
                    },
                    {
                      label: "Branch",
                      href:
                        "https://github.com/D-sorganization/" +
                        repoName +
                        "/tree/" +
                        encodeURIComponent(branch),
                    },
                    {
                      label: "Logs",
                      href: ghUrl + "/logs",
                    },
                  ].map(function (link) {
                    return h(
                      "a",
                      {
                        key: link.label,
                        href: link.href,
                        target: "_blank",
                        rel: "noopener noreferrer",
                        onClick: function (e) {
                          e.stopPropagation();
                        },
                        style: {
                          fontSize: 10,
                          color: "var(--accent-green)",
                          textDecoration: "none",
                          padding: "3px 6px",
                          border: "1px solid var(--accent-green)",
                          borderRadius: 4,
                          whiteSpace: "nowrap",
                        },
                      },
                      "↗ " + link.label,
                    );
                  }),
                ),
                h(
                  "select",
                  {
                    value: isSelected
                      ? provider
                      : policy.default_provider || "jules_api",
                    onClick: function (e) {
                      e.stopPropagation();
                      setSelectedRunId(String(run.id));
                    },
                    onChange: function (e) {
                      e.stopPropagation();
                      setSelectedRunId(String(run.id));
                      setProvider(e.target.value);
                    },
                    style: {
                      background: "var(--bg-primary)",
                      color: "var(--text-primary)",
                      border: "1px solid var(--border)",
                      borderRadius: 6,
                      padding: "4px 8px",
                      fontSize: 12,
                    },
                  },
                  providerEntries.map(function (entry) {
                    return h(
                      "option",
                      { key: entry[0], value: entry[0] },
                      entry[1].label || entry[0],
                    );
                  }),
                ),
                (function() {
                  var currentProvider = isSelected ? provider : (policy.default_provider || "jules_api");
                  var modelOpts = _PROVIDER_MODELS[currentProvider];
                  if (!modelOpts || !isSelected) return null;
                  return h(
                    "select",
                    {
                      value: model || (modelOpts[0] && modelOpts[0].value) || "",
                      onClick: function (e) { e.stopPropagation(); },
                      onChange: function (e) {
                        e.stopPropagation();
                        setModel(e.target.value);
                      },
                      style: {
                        background: "var(--bg-primary)",
                        color: "var(--text-muted)",
                        border: "1px solid var(--border)",
                        borderRadius: 6,
                        padding: "4px 8px",
                        fontSize: 11,
                      },
                    },
                    modelOpts.map(function (m) {
                      return h("option", { key: m.value, value: m.value }, m.label);
                    }),
                  );
                })(),
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function (e) {
                      e.stopPropagation();
                      setSelectedRunId(String(run.id));
                      onPreview(run);
                    },
                    disabled: loading,
                    style: { whiteSpace: "nowrap" },
                  },
                  "Preview",
                ),
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function (e) {
                      e.stopPropagation();
                      setSelectedRunId(String(run.id));
                      onDispatch(run);
                    },
                    disabled: loading || (isSelected && !accepted),
                    style: {
                      whiteSpace: "nowrap",
                      background:
                        accepted && isSelected
                          ? "rgba(63,185,80,0.2)"
                          : undefined,
                    },
                  },
                  "Dispatch",
                ),
              );
            }),
      ),
    ),
    // ── Stat row with inline-editable fields ──────────────────────────
    h(
      "div",
      { className: "stat-row" },
      h(
        "div",
        {
          className: "stat-card",
          style: { cursor: "pointer" },
          onClick: function () {
            setEditingLoopGuard(true);
          },
        },
        h("div", { className: "stat-label" }, "Loop guard"),
        editingLoopGuard
          ? h(
              "div",
              {
                style: { display: "flex", gap: 6, alignItems: "center" },
              },
              h("input", {
                type: "number",
                min: 1,
                max: 20,
                value: loopGuardValue,
                autoFocus: true,
                onChange: function (e) {
                  setLoopGuardValue(e.target.value);
                },
                onKeyDown: function (e) {
                  if (e.key === "Enter") saveLoopGuard();
                  if (e.key === "Escape") setEditingLoopGuard(false);
                },
                style: {
                  width: 60,
                  background: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--accent-green)",
                  borderRadius: 4,
                  padding: "2px 6px",
                  fontSize: 18,
                  fontWeight: 700,
                },
              }),
              h(
                "button",
                {
                  className: "btn",
                  onClick: function (e) {
                    e.stopPropagation();
                    saveLoopGuard();
                  },
                  style: { padding: "2px 8px", fontSize: 11 },
                },
                "Save",
              ),
            )
          : h(
              "div",
              { style: { fontSize: 24, fontWeight: 700 } },
              policy.max_same_failure_attempts != null
                ? policy.max_same_failure_attempts
                : 3,
            ),
        h(
          "div",
          { className: "stat-sub" },
          editingLoopGuard ? "press Enter to save" : "click to edit",
        ),
      ),
      h(
        "div",
        {
          className: "stat-card",
          style: { cursor: "pointer" },
          onClick: function () {
            setEditingDefaultProvider(true);
          },
        },
        h("div", { className: "stat-label" }, "Default provider"),
        editingDefaultProvider
          ? h(
              "select",
              {
                autoFocus: true,
                value: policy.default_provider || "jules_api",
                onChange: function (e) {
                  saveDefaultProvider(e.target.value);
                },
                onBlur: function () {
                  setEditingDefaultProvider(false);
                },
                style: {
                  background: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--accent-green)",
                  borderRadius: 4,
                  padding: "2px 6px",
                  fontSize: 13,
                  fontWeight: 700,
                },
              },
              providerEntries.map(function (entry) {
                return h(
                  "option",
                  { key: entry[0], value: entry[0] },
                  entry[1].label || entry[0],
                );
              }),
            )
          : h(
              "div",
              { style: { fontSize: 16, fontWeight: 700 } },
              policy.default_provider || "jules_api",
            ),
        h(
          "div",
          { className: "stat-sub" },
          editingDefaultProvider ? "select to save" : "click to edit",
        ),
      ),
      h(Stat, {
        label: "Failed runs",
        value: failedRuns.length,
        sub: "current dashboard sample",
      }),
      h(Stat, {
        label: "Dispatch history",
        value: history.length,
        sub: "recent dispatches",
      }),
      h(Stat, {
        label: "Jules workflows",
        value: (workflows.workflows || []).length,
        sub: "health visibility",
      }),
    ),
    // ── Two-column grid ───────────────────────────────────────────────
    h(
      "div",
      {
        style: {
          display: "grid",
          gridTemplateColumns: "minmax(320px, 420px) 1fr",
          gap: 16,
          marginTop: 16,
        },
      },
      // Left column: Auto config + Providers
      h(
        "div",
        null,
        h(
          "div",
          { className: "section" },
          h(
            "div",
            { className: "section-header" },
            h(
              "span",
              { className: "section-title" },
              I.settings(14),
              "Automatic remediation configuration",
            ),
            h(
              "button",
              {
                className: "btn",
                onClick: function () {
                  savePolicy();
                },
                disabled: savingPolicy || loading,
              },
              savingPolicy ? "Saving…" : "Save routing",
            ),
          ),
          h(
            "div",
            { className: "section-body" },
            h(
              "div",
              {
                style: {
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  marginBottom: 12,
                },
              },
              "Workflow Type Routing lets simple failures auto-dispatch while complex failures can stay manual until reviewed.",
            ),
            h(
              "div",
              {
                style: {
                  fontSize: 13,
                  fontWeight: 600,
                  marginBottom: 10,
                },
              },
              "Workflow Type Routing",
            ),
            Object.keys(draftRules).map(function (workflowType) {
              var rule = draftRules[workflowType] || {};
              return h(
                "div",
                {
                  key: workflowType,
                  style: {
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    padding: 12,
                    marginBottom: 10,
                  },
                },
                h(
                  "div",
                  {
                    style: {
                      display: "grid",
                      gridTemplateColumns: "1.2fr 1fr 1fr",
                      gap: 10,
                      marginBottom: 8,
                    },
                  },
                  h(
                    "div",
                    null,
                    h(
                      "div",
                      { style: { fontSize: 13, fontWeight: 600 } },
                      rule.label || workflowType,
                    ),
                    h(
                      "div",
                      {
                        style: {
                          marginTop: 4,
                          fontSize: 11,
                          color: "var(--text-muted)",
                        },
                      },
                      (rule.match_terms || []).join(", ") || "fallback",
                    ),
                  ),
                  h(
                    "label",
                    {
                      style: {
                        fontSize: 12,
                        color: "var(--text-secondary)",
                      },
                    },
                    "Dispatch mode",
                    h(
                      "select",
                      {
                        value: rule.dispatch_mode || "manual",
                        onChange: function (e) {
                          updateRule(
                            workflowType,
                            "dispatch_mode",
                            e.target.value,
                          );
                        },
                        style: {
                          width: "100%",
                          marginTop: 6,
                          background: "var(--bg-primary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border)",
                          borderRadius: 6,
                          padding: "8px 10px",
                        },
                      },
                      h("option", { value: "auto" }, "Auto"),
                      h("option", { value: "manual" }, "Manual"),
                    ),
                  ),
                  h(
                    "label",
                    {
                      style: {
                        fontSize: 12,
                        color: "var(--text-secondary)",
                      },
                    },
                    "Provider",
                    h(
                      "select",
                      {
                        value:
                          rule.provider_id ||
                          policy.default_provider ||
                          "jules_api",
                        onChange: function (e) {
                          updateRule(
                            workflowType,
                            "provider_id",
                            e.target.value,
                          );
                        },
                        style: {
                          width: "100%",
                          marginTop: 6,
                          background: "var(--bg-primary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border)",
                          borderRadius: 6,
                          padding: "8px 10px",
                        },
                      },
                      providerEntries.map(function (entry) {
                        return h(
                          "option",
                          {
                            key: workflowType + "-" + entry[0],
                            value: entry[0],
                          },
                          entry[1].label || entry[0],
                        );
                      }),
                    ),
                  ),
                ),
                h(
                  "label",
                  {
                    style: {
                      fontSize: 12,
                      color: "var(--text-secondary)",
                    },
                  },
                  "Fallback providers (loop guard escalation)",
                  h(
                    "select",
                    {
                      multiple: true,
                      value: rule.fallback_providers || [],
                      onChange: function (e) {
                        var selected = [];
                        for (
                          var i = 0;
                          i < e.target.options.length;
                          i++
                        ) {
                          if (e.target.options[i].selected) {
                            selected.push(e.target.options[i].value);
                          }
                        }
                        updateRule(
                          workflowType,
                          "fallback_providers",
                          selected,
                        );
                      },
                      style: {
                        width: "100%",
                        marginTop: 6,
                        background: "var(--bg-primary)",
                        color: "var(--text-primary)",
                        border: "1px solid var(--border)",
                        borderRadius: 6,
                        padding: "4px 6px",
                        height: 72,
                      },
                    },
                    providerEntries.map(function (entry) {
                      return h(
                        "option",
                        {
                          key: workflowType + "-fb-" + entry[0],
                          value: entry[0],
                        },
                        entry[1].label || entry[0],
                      );
                    }),
                  ),
                ),
              );
            }),
          ),
        ),
        h(
          "div",
          { className: "section", style: { marginTop: 16 } },
          h(
            "div",
            { className: "section-header" },
            h(
              "span",
              { className: "section-title" },
              I.server(14),
              "Providers",
            ),
          ),
          h(
            "div",
            { className: "section-body" },
            providerEntries.map(function (entry) {
              var providerId = entry[0];
              var providerMeta = entry[1];
              var state = availability[providerId] || {};
              return h(
                "div",
                {
                  key: providerId,
                  style: {
                    padding: "10px 0",
                    borderBottom: "1px solid var(--border)",
                  },
                },
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 12,
                    },
                  },
                  h(
                    "span",
                    { style: { fontSize: 13, fontWeight: 600 } },
                    providerMeta.label,
                  ),
                  h(
                    "span",
                    {
                      className: "section-badge",
                      style: {
                        background: state.available
                          ? "rgba(63,185,80,0.15)"
                          : "rgba(210,153,34,0.15)",
                        color: state.available
                          ? "var(--accent-green)"
                          : "var(--accent-yellow)",
                      },
                    },
                    state.status || "unknown",
                  ),
                ),
                h(
                  "div",
                  {
                    style: {
                      marginTop: 4,
                      fontSize: 12,
                      color: "var(--text-muted)",
                    },
                  },
                  providerMeta.notes || "",
                ),
              );
            }),
          ),
        ),
      ),
      // Right column: History + Plan Preview + Jules Workflow Health
      h(
        "div",
        null,
        h(
          "div",
          { className: "section" },
          h(
            "div",
            { className: "section-header" },
            h(
              "span",
              { className: "section-title" },
              I.clock(14),
              "Remediation History",
            ),
          ),
          h(
            "div",
            { className: "section-body" },
            history.length === 0
              ? h(
                  "div",
                  { style: { color: "var(--text-muted)", fontSize: 12 } },
                  "No dispatch history yet. History is recorded after each manual dispatch.",
                )
              : history.map(function (entry, idx) {
                  var ts = entry.timestamp
                    ? entry.timestamp.replace("T", " ").slice(0, 19) +
                      " UTC"
                    : "";
                  var outcome = entry.status || "dispatched";
                  return h(
                    "div",
                    {
                      key: idx,
                      style: {
                        padding: "10px 0",
                        borderBottom: "1px solid var(--border)",
                      },
                    },
                    h(
                      "div",
                      {
                        style: {
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 8,
                        },
                      },
                      h(
                        "span",
                        { style: { fontSize: 12, fontWeight: 600 } },
                        (entry.repository || "unknown") +
                          " \xB7 " +
                          (entry.workflow_name || "workflow"),
                      ),
                      h(
                        "span",
                        {
                          className: "section-badge",
                          style: {
                            background:
                              outcome === "dispatched"
                                ? "rgba(63,185,80,0.15)"
                                : "rgba(248,81,73,0.15)",
                            color:
                              outcome === "dispatched"
                                ? "var(--accent-green)"
                                : "var(--accent-red)",
                          },
                        },
                        outcome,
                      ),
                    ),
                    h(
                      "div",
                      {
                        style: {
                          marginTop: 3,
                          fontSize: 11,
                          color: "var(--text-muted)",
                        },
                      },
                      ts +
                        (entry.provider
                          ? " \xB7 " + entry.provider
                          : "") +
                        (entry.branch ? " \xB7 " + entry.branch : "") +
                        (entry.run_id ? " \xB7 #" + entry.run_id : ""),
                    ),
                  );
                }),
          ),
        ),
        h(
          "div",
          { className: "section", style: { marginTop: 16 } },
          h(
            "div",
            { className: "section-header" },
            h(
              "span",
              { className: "section-title" },
              I.activity(14),
              "Plan Preview",
            ),
          ),
          h(
            "div",
            { className: "section-body" },
            !plan
              ? h(
                  "div",
                  {
                    style: { color: "var(--text-muted)", fontSize: 12 },
                  },
                  "Select a failed run above and click Preview.",
                )
              : [
                  h(
                    "div",
                    {
                      key: "summary",
                      style: {
                        display: "flex",
                        gap: 8,
                        alignItems: "center",
                        flexWrap: "wrap",
                        marginBottom: 12,
                      },
                    },
                    h(
                      "span",
                      {
                        className: "section-badge",
                        style: {
                          background: accepted
                            ? "rgba(63,185,80,0.15)"
                            : "rgba(248,81,73,0.15)",
                          color: accepted
                            ? "var(--accent-green)"
                            : "var(--accent-red)",
                        },
                      },
                      accepted ? "dispatch allowed" : "blocked",
                    ),
                    h(
                      "span",
                      {
                        style: {
                          fontSize: 12,
                          color: "var(--text-secondary)",
                        },
                      },
                      plan.decision && plan.decision.reason
                        ? plan.decision.reason
                        : "",
                    ),
                  ),
                  h(
                    "div",
                    {
                      key: "attempts",
                      style: {
                        display: "grid",
                        gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                        gap: 8,
                        marginBottom: 12,
                      },
                    },
                    h(
                      "div",
                      { className: "stat-card", style: { padding: 10 } },
                      h("div", { className: "stat-label" }, "Attempts"),
                      h(
                        "div",
                        { style: { fontSize: 16, fontWeight: 700 } },
                        (plan.decision && plan.decision.attempt_count) ||
                          0,
                      ),
                    ),
                    h(
                      "div",
                      { className: "stat-card", style: { padding: 10 } },
                      h("div", { className: "stat-label" }, "Remaining"),
                      h(
                        "div",
                        { style: { fontSize: 16, fontWeight: 700 } },
                        plan.decision &&
                          plan.decision.remaining_attempts != null
                          ? plan.decision.remaining_attempts
                          : "-",
                      ),
                    ),
                    h(
                      "div",
                      { className: "stat-card", style: { padding: 10 } },
                      h("div", { className: "stat-label" }, "Provider"),
                      h(
                        "div",
                        { style: { fontSize: 16, fontWeight: 700 } },
                        (plan.decision && plan.decision.provider_id) ||
                          provider,
                      ),
                    ),
                  ),
                  h("pre", {
                    key: "prompt",
                    style: {
                      margin: 0,
                      padding: 12,
                      background: "var(--bg-secondary)",
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      color: "var(--text-secondary)",
                      fontSize: 12,
                      whiteSpace: "pre-wrap",
                      maxHeight: 280,
                      overflow: "auto",
                    },
                    children:
                      (plan.decision && plan.decision.prompt_preview) ||
                      "(no prompt preview returned)",
                  }),
                  dispatchState
                    ? h(
                        "div",
                        {
                          key: "dispatch",
                          style: {
                            marginTop: 12,
                            fontSize: 12,
                            color: dispatchState.error
                              ? "var(--accent-red)"
                              : "var(--accent-green)",
                          },
                        },
                        dispatchState.error || dispatchState.note,
                      )
                    : null,
                ],
          ),
        ),
        julesDispatchMsg
          ? h(
              "div",
              {
                role: "alert",
                style: {
                  margin: "12px 0 0",
                  padding: "10px 16px",
                  borderRadius: 6,
                  background:
                    julesDispatchMsg.type === "error"
                      ? "rgba(248,81,73,0.15)"
                      : "rgba(63,185,80,0.15)",
                  color:
                    julesDispatchMsg.type === "error"
                      ? "var(--accent-red)"
                      : "var(--accent-green)",
                  border:
                    "1px solid " +
                    (julesDispatchMsg.type === "error"
                      ? "var(--accent-red)"
                      : "var(--accent-green)"),
                  fontSize: 13,
                },
              },
              julesDispatchMsg.text,
            )
          : null,
        h(
          "div",
          { className: "section", style: { marginTop: 16 } },
          h(
            "div",
            { className: "section-header" },
            h(
              "span",
              { className: "section-title" },
              I.clock(14),
              "Jules Workflow Health",
            ),
          ),
          h(
            "div",
            { className: "section-body" },
            workflows.control_tower_summary
              ? h(
                  "div",
                  {
                    style: {
                      marginBottom: 12,
                      padding: "10px 12px",
                      borderRadius: 8,
                      background: "rgba(210,153,34,0.15)",
                      color: "var(--accent-yellow)",
                      fontSize: 12,
                    },
                  },
                  workflows.control_tower_summary,
                )
              : null,
            ((workflows.workflows || []).length === 0
              ? [
                  h(
                    "div",
                    {
                      key: "empty",
                      style: {
                        color: "var(--text-muted)",
                        fontSize: 12,
                      },
                    },
                    "No Jules workflow health data loaded yet.",
                  ),
                ]
              : workflows.workflows
            ).map(function (entry, idx) {
              if (entry.workflow_file) {
                var ghActionsLink =
                  "https://github.com/D-sorganization/Repository_Management/actions/workflows/" +
                  entry.workflow_file;
                var triggerType = entry.trigger_type || "dormant";
                var triggerColor =
                  triggerType === "manual"
                    ? "var(--accent-blue)"
                    : triggerType === "scheduled"
                      ? "var(--accent-purple)"
                      : triggerType === "workflow_run"
                        ? "var(--text-secondary)"
                        : "var(--accent-yellow)";
                var triggerBg =
                  triggerType === "manual"
                    ? "rgba(88,166,255,0.15)"
                    : triggerType === "scheduled"
                      ? "rgba(163,113,247,0.15)"
                      : triggerType === "workflow_run"
                        ? "rgba(139,148,158,0.15)"
                        : "rgba(227,179,65,0.15)";
                var ghLink =
                  "https://github.com/D-sorganization/Repository_Management/blob/main/.github/workflows/" +
                  entry.workflow_file;
                return h(
                  "div",
                  {
                    key: entry.workflow_file,
                    style: {
                      padding: "10px 0",
                      borderBottom: "1px solid var(--border)",
                    },
                  },
                  h(
                    "div",
                    {
                      style: {
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 12,
                        alignItems: "center",
                      },
                    },
                    h(
                      "a",
                      {
                        href: ghActionsLink,
                        target: "_blank",
                        rel: "noopener noreferrer",
                        style: {
                          fontSize: 13,
                          fontWeight: 600,
                          color: "var(--text-primary)",
                          textDecoration: "none",
                        },
                      },
                      entry.workflow_name,
                    ),
                    h(
                      "span",
                      {
                        style: {
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                        },
                      },
                      h(
                        "a",
                        {
                          href: ghLink,
                          target: "_blank",
                          rel: "noopener noreferrer",
                          style: {
                            fontSize: 13,
                            fontWeight: 600,
                            color: "var(--text-primary)",
                            textDecoration: "none",
                          },
                        },
                        entry.workflow_name,
                      ),
                      h(
                        "span",
                        {
                          className: "section-badge",
                          style: {
                            background: triggerBg,
                            color: triggerColor,
                          },
                        },
                        triggerType,
                      ),
                    ),
                    h(
                      "div",
                      {
                        style: {
                          display: "flex",
                          gap: 6,
                          alignItems: "center",
                        },
                      },
                      "manual dispatch: " +
                      String(entry.manual_dispatch) +
                      " \xB7 scheduled: " +
                      String(entry.scheduled) +
                      " \xB7 workflow_run: " +
                      String(entry.workflow_run_trigger),
                      h(
                        "span",
                        {
                          className: "section-badge",
                          style: {
                            background:
                              (entry.issues || []).length > 0
                                ? "rgba(248,81,73,0.15)"
                                : "rgba(63,185,80,0.15)",
                            color:
                              (entry.issues || []).length > 0
                                ? "var(--accent-red)"
                                : "var(--accent-green)",
                          },
                        },
                        (entry.issues || []).length > 0
                          ? (entry.issues || []).length + " issue(s)"
                          : "healthy",
                      ),
                      triggerType === "manual"
                        ? h(
                            "button",
                            {
                              style: {
                                fontSize: 11,
                                padding: "2px 8px",
                                borderRadius: 4,
                                border: "1px solid #58a6ff",
                                background: "rgba(88,166,255,0.1)",
                                color: "var(--accent-blue)",
                                cursor: "pointer",
                              },
                              onClick: function () {
                                legacyFetch(
                                  "/api/agent-remediation/dispatch-jules",
                                  {
                                    method: "POST",
                                    headers: {
                                      "Content-Type": "application/json",
                                      "X-Requested-With": "XMLHttpRequest",
                                    },
                                    body: JSON.stringify({
                                      workflow_file: entry.workflow_file,
                                      ref: "main",
                                      inputs: {},
                                    }),
                                  },
                                )
                                  .then(function (r) {
                                    if (!r.ok) {
                                      return r.json().then(function (e) {
                                        setJulesDispatchMsg({ type: "error", text: "Dispatch failed: " + (e.detail || r.status) });
                                        setTimeout(function () { setJulesDispatchMsg(null); }, 6000);
                                      });
                                    }
                                    setJulesDispatchMsg({ type: "success", text: "Dispatched " + entry.workflow_file });
                                    setTimeout(function () { setJulesDispatchMsg(null); }, 6000);
                                  })
                                  .catch(function (err) {
                                    setJulesDispatchMsg({ type: "error", text: "Dispatch error: " + err });
                                    setTimeout(function () { setJulesDispatchMsg(null); }, 6000);
                                  });
                              },
                            },
                            "Run",
                          )
                        : null,
                    ),
                  ),
                  (entry.issues || []).map(function (issue, issueIndex) {
                    return h(
                      "div",
                      {
                        key: entry.workflow_file + "-" + issueIndex,
                        style: {
                          marginTop: 6,
                          fontSize: 12,
                          color: "var(--text-secondary)",
                        },
                      },
                      issue,
                    );
                  }),
                );
              }
              return entry;
            }),
          ),
        ),
      ),
    ),
    ),
    subTab === "prs" && h(PRsSubTab, {}),
    subTab === "issues" && h(IssuesSubTab, {}),
  );
}

function DashboardHelp(p) {
  var currentTab = p.currentTab || "";
  var open = React.useState(false);
  var isOpen = open[0],
    setIsOpen = open[1];
  return h(
    "div",
    { style: { position: "fixed", bottom: 20, right: 20, zIndex: 500 } },
    !isOpen
      ? h(
          "button",
          {
            onClick: function () {
              setIsOpen(true);
            },
            title: "Dashboard help",
            style: {
              width: 48,
              height: 48,
              borderRadius: "50%",
              background: "var(--accent-purple, #886ce4)",
              color: "var(--text-on-accent)",
              border: "none",
              cursor: "pointer",
              fontSize: 20,
              boxShadow: "0 2px 12px rgba(0,0,0,0.3)",
            },
          },
          "?",
        )
      : h(
          "div",
          {
            style: {
              width: 320,
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
              padding: 16,
            },
          },
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 } },
            h("strong", null, "Dashboard Help"),
            h("button", { onClick: function () { setIsOpen(false); }, className: "btn", style: { padding: "2px 8px" }, "aria-label": "Close assessment dialog" }, "Close"),
          ),
          h("div", { style: { fontSize: 12, color: "var(--text-secondary)" } }, "Current tab: " + currentTab),
        ),
  );
}


// ════════════════════════ ASSISTANT SIDEBAR ════════════════════════

/** Minimal Markdown renderer — bold, italic, inline code, code blocks, links, lists */
function renderMarkdown(text) {
  if (!text) return [];
  var out = [];
  var lines = text.split("\n");
  var i = 0;
  while (i < lines.length) {
    var line = lines[i];
    // Fenced code block
    if (line.startsWith("```")) {
      var codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      out.push(h("pre", { key: out.length, style: { background: "var(--bg-tertiary)", borderRadius: 6, padding: "10px 12px", overflowX: "auto", fontSize: 12, margin: "6px 0" } },
        h("code", null, codeLines.join("\n"))
      ));
      i++;
      continue;
    }
    // Unordered list item
    if (/^[-*] /.test(line)) {
      var listItems = [];
      while (i < lines.length && /^[-*] /.test(lines[i])) {
        listItems.push(h("li", { key: i }, inlineMarkdown(lines[i].slice(2))));
        i++;
      }
      out.push(h("ul", { key: out.length, style: { paddingLeft: 18, margin: "4px 0" } }, listItems));
      continue;
    }
    // Ordered list item
    if (/^\d+\. /.test(line)) {
      var olItems = [];
      while (i < lines.length && /^\d+\. /.test(lines[i])) {
        olItems.push(h("li", { key: i }, inlineMarkdown(lines[i].replace(/^\d+\. /, ""))));
        i++;
      }
      out.push(h("ol", { key: out.length, style: { paddingLeft: 18, margin: "4px 0" } }, olItems));
      continue;
    }
    // Heading
    var hm = line.match(/^(#{1,3}) (.+)/);
    if (hm) {
      var lvl = hm[1].length;
      var tag = "h" + (lvl + 3);
      out.push(h(tag, { key: out.length, style: { margin: "8px 0 4px", fontWeight: 600, fontSize: lvl === 1 ? 15 : lvl === 2 ? 13 : 12 } }, inlineMarkdown(hm[2])));
      i++;
      continue;
    }
    // Blank line
    if (line.trim() === "") {
      out.push(h("br", { key: out.length }));
      i++;
      continue;
    }
    // Paragraph
    out.push(h("p", { key: out.length, style: { margin: "2px 0" } }, inlineMarkdown(line)));
    i++;
  }
  return out;
}

function inlineMarkdown(text) {
  // Split on inline code, bold, italic, links
  var parts = [];
  var re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[([^\]]+)\]\(([^)]+)\))/g;
  var last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    var tok = m[0];
    if (tok.startsWith("`")) {
      parts.push(h("code", { key: parts.length, style: { background: "var(--bg-tertiary)", borderRadius: 3, padding: "1px 5px", fontSize: "0.9em", fontFamily: "monospace" } }, tok.slice(1, -1)));
    } else if (tok.startsWith("**")) {
      parts.push(h("strong", { key: parts.length }, tok.slice(2, -2)));
    } else if (tok.startsWith("*")) {
      parts.push(h("em", { key: parts.length }, tok.slice(1, -1)));
    } else {
      parts.push(h("a", { key: parts.length, href: m[3], target: "_blank", rel: "noopener noreferrer", style: { color: "var(--accent-blue)" } }, m[2]));
    }
    last = re.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

var ASST_LS = {
  open: "assistant:open",
  position: "assistant:position",
  width: "assistant:width",
  transcript: "assistant:transcript",
  transcriptTimestamp: "assistant:transcript:ts",
  openByDefault: "assistant:openByDefault",
  includeContext: "assistant:includeContext",
  saveHistory: "assistant:saveHistory",
};

var ASST_HISTORY_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

function lsLoadTranscript() {
  try {
    var ts = parseInt(localStorage.getItem(ASST_LS.transcriptTimestamp) || "0", 10);
    if (!ts || Date.now() - ts > ASST_HISTORY_TTL_MS) {
      localStorage.removeItem(ASST_LS.transcript);
      localStorage.removeItem(ASST_LS.transcriptTimestamp);
      return [];
    }
    return lsGet(ASST_LS.transcript, []);
  } catch (e) {
    return [];
  }
}

function lsGet(key, fallback) {
  try { var v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
}
function lsSet(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {}
}

function clearAssistantTranscriptHistory() {
  try {
    localStorage.removeItem(ASST_LS.transcript);
    localStorage.removeItem(ASST_LS.transcriptTimestamp);
  } catch (e) {}
}

function AssistantSidebar(props) {
  var currentTab = props.currentTab || "";
  var open = props.open;
  var toggle = props.onToggle;

  var ps2 = React.useState(lsGet(ASST_LS.position, "right"));
  var position = ps2[0], setPosition = ps2[1];

  var ws2 = React.useState(lsGet(ASST_LS.width, 360));
  var width = ws2[0], setWidth = ws2[1];

  var sh2 = React.useState(lsGet(ASST_LS.saveHistory, false));
  var saveHistory = sh2[0], setSaveHistory = sh2[1];

  var ts2 = React.useState(function () { return saveHistory ? lsLoadTranscript() : []; });
  var transcript = ts2[0], setTranscript = ts2[1];

  var ic2 = React.useState(lsGet(ASST_LS.includeContext, true));
  var includeCtx = ic2[0], setIncludeCtx = ic2[1];

  var obds = React.useState(lsGet(ASST_LS.openByDefault, false));
  var openByDefault = obds[0], setOpenByDefault = obds[1];

  var inputS = React.useState("");
  var inputVal = inputS[0], setInputVal = inputS[1];

  var loadS = React.useState(false);
  var loading = loadS[0], setLoading = loadS[1];

  var showSettingsS = React.useState(false);
  var showSettings = showSettingsS[0], setShowSettings = showSettingsS[1];

  var transcriptRef = React.useRef(null);
  var dragStartX = React.useRef(null);
  var dragStartW = React.useRef(null);

  // Scroll to bottom when transcript changes
  React.useEffect(function () {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [transcript, open]);

  // Persist state changes
  React.useEffect(function () { lsSet(ASST_LS.position, position); }, [position]);
  React.useEffect(function () { lsSet(ASST_LS.width, width); }, [width]);
  React.useEffect(function () { lsSet(ASST_LS.includeContext, includeCtx); }, [includeCtx]);
  React.useEffect(function () { lsSet(ASST_LS.openByDefault, openByDefault); }, [openByDefault]);
  React.useEffect(function () { lsSet(ASST_LS.saveHistory, saveHistory); }, [saveHistory]);
  React.useEffect(function () {
    if (!saveHistory) {
      clearAssistantTranscriptHistory();
      return;
    }
    var capped = transcript.length > 200 ? transcript.slice(-200) : transcript;
    lsSet(ASST_LS.transcript, capped);
    try { localStorage.setItem(ASST_LS.transcriptTimestamp, String(Date.now())); } catch (e) {}
  }, [transcript, saveHistory]);

  function getPageContext() {
    return {
      tab: currentTab,
      url: window.location.href,
      selection: window.getSelection ? window.getSelection().toString().slice(0, 500) : "",
    };
  }

  function sendMessage() {
    var msg = inputVal.trim();
    if (!msg || loading) return;
    setInputVal("");
    var userMsg = { role: "user", content: msg, id: Date.now() };
    setTranscript(function (t) { return t.concat([userMsg]); });
    setLoading(true);

    // Build request with context
    var body = {
      prompt: msg,
      context: {
        current_tab: currentTab,
        selected_run_id: null,
        selected_items: [],
      },
    };
    if (includeCtx) {
      var ctx = getPageContext();
      body.context.dashboard_state = ctx;
    }

    legacyFetch("/api/assistant/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        var reply = data.response || data.message || JSON.stringify(data);
        var asstMsg = { role: "assistant", content: reply, id: Date.now() + 1 };
        setTranscript(function (t) { return t.concat([asstMsg]); });
      })
      .catch(function (err) {
        var errMsg = { role: "assistant", content: "Error: " + (err.message || "request failed"), id: Date.now() + 1 };
        setTranscript(function (t) { return t.concat([errMsg]); });
      })
      .finally(function () { setLoading(false); });
  }

  function handleTranscription(text) {
    setInputVal(function (prev) {
      return prev ? prev + " " + text : text;
    });
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function startDrag(e) {
    dragStartX.current = e.clientX;
    dragStartW.current = width;
    e.preventDefault();
    function onMove(ev) {
      var delta = position === "right" ? dragStartX.current - ev.clientX : ev.clientX - dragStartX.current;
      var newW = Math.min(600, Math.max(280, dragStartW.current + delta));
      setWidth(newW);
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // Sidebar container
  var sidebarStyle = {
    width: open ? width : 0,
    minWidth: open ? width : 0,
    maxWidth: open ? width : 0,
    overflow: "hidden",
    transition: prefersReducedMotion() ? "none" : "width 0.2s, min-width 0.2s, max-width 0.2s",
    flexShrink: 0,
    position: "relative",
    background: "var(--bg-secondary)",
    borderLeft: position === "right" ? "1px solid var(--border)" : "none",
    borderRight: position === "left" ? "1px solid var(--border)" : "none",
    display: "flex",
    flexDirection: "column",
    height: "calc(100vh - 56px)",
    top: 0,
  };

  var dragHandleStyle = {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: 5,
    cursor: "col-resize",
    background: "transparent",
    zIndex: 10,
    left: position === "right" ? 0 : "auto",
    right: position === "left" ? 0 : "auto",
  };

  var headerStyle = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "10px 12px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
    background: "var(--bg-tertiary)",
  };

  var transcriptStyle = {
    flex: 1,
    overflowY: "auto",
    padding: "12px",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };

  var inputAreaStyle = {
    borderTop: "1px solid var(--border)",
    padding: "8px",
    flexShrink: 0,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  };

  var settingsPanel = showSettings
    ? h("div", { style: { padding: "12px", display: "flex", flexDirection: "column", gap: 12, overflowY: "auto", flex: 1 } },
        h("div", { style: { display: "flex", alignItems: "center", gap: 8 } },
          h("button", { onClick: function () { setShowSettings(false); }, style: { background: "none", border: "none", color: "var(--accent-blue)", cursor: "pointer", fontSize: 13 }, "aria-label": "Back to settings" }, "← Back"),
          h("span", { style: { fontWeight: 600, fontSize: 13 } }, "Settings"),
        ),
        h("label", { style: { fontSize: 12, display: "flex", flexDirection: "column", gap: 4 } },
          "Position",
          h("div", { style: { display: "flex", gap: 12, marginTop: 4 } },
            h("label", { style: { display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12 } },
              h("input", { type: "radio", name: "asst-pos", checked: position === "right", onChange: function () { setPosition("right"); }, style: { accentColor: "var(--accent-blue)" } }),
              "Right"
            ),
            h("label", { style: { display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12 } },
              h("input", { type: "radio", name: "asst-pos", checked: position === "left", onChange: function () { setPosition("left"); }, style: { accentColor: "var(--accent-blue)" } }),
              "Left"
            ),
          ),
        ),
        h("label", { style: { fontSize: 12, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" } },
          h("input", { type: "checkbox", checked: openByDefault, onChange: function (e) { setOpenByDefault(e.target.checked); }, style: { accentColor: "var(--accent-blue)" } }),
          "Open by default"
        ),
        h("label", { style: { fontSize: 12, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" } },
          h("input", { type: "checkbox", checked: includeCtx, onChange: function (e) { setIncludeCtx(e.target.checked); }, style: { accentColor: "var(--accent-blue)" } }),
          "Include page context with messages"
        ),
        h("label", { style: { fontSize: 12, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" } },
          h("input", {
            type: "checkbox",
            checked: saveHistory,
            onChange: function (e) {
              var next = e.target.checked;
              setSaveHistory(next);
              if (!next) {
                setTranscript([]);
                clearAssistantTranscriptHistory();
              }
            },
            style: { accentColor: "var(--accent-blue)" },
          }),
          "Save chat history"
        ),
        h("button", {
          onClick: function () {
            setTranscript([]);
            clearAssistantTranscriptHistory();
            setShowSettings(false);
          },
          style: { background: "var(--accent-red)", color: "var(--text-on-accent)", border: "none", borderRadius: 6, padding: "6px 12px", cursor: "pointer", fontSize: 12, width: "100%", marginTop: 8 },
        }, "Clear chat history"),
      )
    : null;

  var chatPanel = !showSettings
    ? h(React.Fragment, null,
        h("div", { ref: transcriptRef, style: transcriptStyle },
          transcript.length === 0
            ? h("div", { style: { color: "var(--text-muted)", fontSize: 12, textAlign: "center", marginTop: 24 } }, "Ask anything about the dashboard…")
            : transcript.map(function (msg) {
                var isUser = msg.role === "user";
                var bubbleStyle = {
                  alignSelf: isUser ? "flex-end" : "flex-start",
                  background: isUser ? "var(--accent-blue)" : "var(--bg-tertiary)",
                  color: isUser ? "var(--text-on-accent)" : "var(--text-primary)",
                  borderRadius: isUser ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
                  padding: "8px 12px",
                  maxWidth: "92%",
                  fontSize: 13,
                  lineHeight: 1.5,
                  wordBreak: "break-word",
                };
                return h("div", { key: msg.id, style: bubbleStyle },
                  isUser ? msg.content : renderMarkdown(msg.content)
                );
              }),
          loading ? h("div", { style: { alignSelf: "flex-start", color: "var(--text-muted)", fontSize: 12, fontStyle: "italic" } }, "Thinking…") : null,
        ),
        h("div", { style: inputAreaStyle },
          h("textarea", {
            value: inputVal,
            onChange: function (e) { setInputVal(e.target.value); },
            onKeyDown: onKeyDown,
            placeholder: "Ask a question… (Enter to send)",
            rows: 3,
            style: {
              width: "100%",
              background: "var(--bg-tertiary)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              color: "var(--text-primary)",
              padding: "8px",
              fontSize: 13,
              resize: "none",
              fontFamily: "inherit",
              outline: "none",
            },
          }),
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
            h("span", { style: { fontSize: 11, color: "var(--text-muted)" } }, "Shift+Enter for newline"),
            h("div", { style: { display: "flex", gap: 8, alignItems: "center" } },
              h(VoiceInputButton, { onTranscription: handleTranscription, disabled: loading }),
              h("button", {
                onClick: sendMessage,
                disabled: loading || !inputVal.trim(),
                style: {
                  background: "var(--accent-blue)",
                  color: "var(--text-on-accent)",
                  border: "none",
                  borderRadius: 6,
                  padding: "5px 14px",
                  cursor: loading || !inputVal.trim() ? "default" : "pointer",
                  fontSize: 13,
                  opacity: loading || !inputVal.trim() ? 0.5 : 1,
                },
              }, "Send"),
            ),
          ),
        ),
      )
    : null;

  return h("div", {
    style: sidebarStyle,
    // a11y (#833): a bare <div> may not carry aria-label (aria-prohibited-attr).
    // Promote the chat panel to a complementary landmark so the label is valid
    // and the region is reachable via the screen-reader landmark rotor.
    role: "complementary",
    "aria-label": "Chat sidebar",
    "aria-hidden": open ? undefined : "true",
  },
    open ? h("div", { style: dragHandleStyle, onMouseDown: startDrag }) : null,
    open ? h(React.Fragment, null,
      h("div", { style: headerStyle },
        h("span", { style: { fontWeight: 600, fontSize: 13 } }, "💬 Chat"),
        h("div", { style: { display: "flex", gap: 6 } },
          h("label", {
            title: "Save chat history",
            style: { display: "flex", alignItems: "center", gap: 4, color: "var(--text-muted)", cursor: "pointer", fontSize: 11 },
          },
            h("input", {
              type: "checkbox",
              checked: saveHistory,
              "aria-label": "Save chat history",
              onChange: function (e) {
                var next = e.target.checked;
                setSaveHistory(next);
                if (!next) {
                  setTranscript([]);
                  clearAssistantTranscriptHistory();
                }
              },
              style: { accentColor: "var(--accent-blue)" },
            }),
            "History"
          ),
          h("button", {
            onClick: function () { setShowSettings(function (s) { return !s; }); },
            title: "Settings",
            "aria-label": "Assistant settings",
            "aria-expanded": showSettings ? "true" : "false",
            style: { background: "none", border: "none", color: showSettings ? "var(--accent-blue)" : "var(--text-muted)", cursor: "pointer", fontSize: 15, lineHeight: 1 },
          }, "⚙️"),
          h("button", {
            onClick: toggle,
            title: "Close",
            "aria-label": "Close assistant",
            style: { background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 16, lineHeight: 1 },
          }, "×"),
        ),
      ),
      settingsPanel,
      chatPanel,
    ) : null,
  );
}

var PROVIDERS_WITH_MODEL = ["claude_code_cli", "codex_cli", "gemini_cli", "jules_api"];

// ════════════════════════ QUICK DISPATCH POPOVER ════════════════════════
function QuickDispatchPopover() {
  var os = React.useState(false);
  var open = os[0], setOpen = os[1];

  var rs = React.useState([]);
  var repoList = rs[0], setRepoList = rs[1];

  var ps = React.useState([]);
  var providerList = ps[0], setProviderList = ps[1];

  var fms = React.useState({
    repository: "",
    provider: "claude_code_cli",
    model: "claude-sonnet-4-6",
    ref: "main",
    prompt: "",
  });
  var form = fms[0], setForm = fms[1];

  var ls = React.useState(false);
  var loading = ls[0], setLoading = ls[1];

  var es = React.useState(null);
  var error = es[0], setError = es[1];

  var ss = React.useState(null);
  var successMsg = ss[0], setSuccessMsg = ss[1];

  var popoverRef = React.useRef(null);
  var triggerRef = React.useRef(null);

  // Close on outside click and Escape
  React.useEffect(function () {
    if (!open) return;
    function onMouseDown(e) {
      if (
        popoverRef.current && !popoverRef.current.contains(e.target) &&
        triggerRef.current && !triggerRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    }
    function onKeyDown(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return function () { 
      document.removeEventListener("mousedown", onMouseDown); 
      document.removeEventListener("keydown", onKeyDown); 
    };
  }, [open]);

  // Fetch repos and providers when popover opens
  React.useEffect(function () {
    if (!open) return;
    if (repoList.length === 0) {
      legacyFetch("/api/repos")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var repos = (d && d.repos) ? d.repos : [];
          setRepoList(repos);
          if (repos.length > 0 && !form.repository) {
            setForm(function (prev) {
              return Object.assign({}, prev, { repository: repos[0].full_name || repos[0].name || "" });
            });
          }
        })
        .catch(function () {});
    }
    if (providerList.length === 0) {
      legacyFetch("/api/agents/providers")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var providers = d && d.providers ? Object.keys(d.providers) : ["claude_code_cli"];
          setProviderList(providers);
        })
        .catch(function () {
          setProviderList(["claude_code_cli", "jules_api", "codex_cli", "gemini_cli"]);
        });
    }
  }, [open]);

  function handleToggle() {
    setOpen(function (prev) { return !prev; });
    setError(null);
    setSuccessMsg(null);
  }

  function handleFormChange(field, value) {
    if (field === "provider") {
      var modelList = _PROVIDER_MODELS[value] || [];
      setForm(function (prev) {
        return Object.assign({}, prev, {
          provider: value,
          model: modelList.length ? modelList[0].value : prev.model,
        });
      });
      return;
    }
    setForm(function (prev) { return Object.assign({}, prev, { [field]: value }); });
  }

  function handleCancel() {
    setOpen(false);
    setError(null);
    setSuccessMsg(null);
  }

  function handleDispatch() {
    setError(null);
    if (!form.repository) {
      setError("Please select a repository.");
      return;
    }
    if (!form.prompt || form.prompt.trim().length < 10) {
      setError("Prompt must be at least 10 characters.");
      return;
    }
    setLoading(true);
    var body = {
      repository: form.repository,
      prompt: form.prompt.trim(),
      provider: form.provider,
      ref: form.ref || "main",
      task_kind: "adhoc",
    };
    if (PROVIDERS_WITH_MODEL.indexOf(form.provider) !== -1 && form.model.trim()) {
      body.model = form.model.trim();
    }
    legacyFetch("/api/agents/quick-dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .then(function (result) {
        setLoading(false);
        if (!result.ok) {
          if (result.status === 429) {
            setError("Rate limited. Try again in a moment.");
          } else {
            setError((result.data && result.data.detail) || "Dispatch failed.");
          }
          return;
        }
        setSuccessMsg("✓ Dispatched!");
        setForm(function (prev) {
          return Object.assign({}, prev, { prompt: "" });
        });
        setTimeout(function () {
          setOpen(false);
          setSuccessMsg(null);
        }, 1800);
      })
      .catch(function () {
        setLoading(false);
        setError("Network error. Please try again.");
      });
  }

  var showModel = PROVIDERS_WITH_MODEL.indexOf(form.provider) !== -1;

  var labelStyle = {
    fontSize: 12,
    color: "var(--text-muted)",
    marginBottom: 3,
    display: "block",
  };
  var inputStyle = {
    width: "100%",
    background: "var(--bg-primary)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    padding: "5px 10px",
    color: "var(--text-primary)",
    fontSize: 13,
    outline: "none",
    boxSizing: "border-box",
  };
  var rowStyle = { marginBottom: 10 };

  return h(
    "div",
    { style: { position: "relative", display: "inline-block" } },
    h(
      "button",
      {
        ref: triggerRef,
        className: "btn btn-blue",
        style: {
          fontSize: 13,
          padding: "6px 12px",
          fontWeight: 600,
          background: "rgba(88,166,255,0.15)",
        },
        onClick: handleToggle,
        title: "Open Quick Dispatch",
        "aria-label": "Open Quick Dispatch",
        "aria-expanded": open,
      },
      "⚡ Quick Dispatch ▾",
    ),
    open
      ? h(
          "div",
          {
            ref: popoverRef,
            role: "dialog",
            "aria-modal": "true",
            "aria-label": "Quick Dispatch",
            style: {
              position: "fixed",
              right: 16,
              top: 64,
              width: "calc(100vw - 32px)",
              maxWidth: 320,
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
              zIndex: 9000,
              padding: 16,
            },
          },
          h(
            "div",
            { style: { fontWeight: 700, fontSize: 14, marginBottom: 14, color: "var(--text-primary)" } },
            "⚡ Quick Dispatch",
          ),
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Repository"),
            h(
              "select",
              {
                style: inputStyle,
                value: form.repository,
                onChange: function (e) { handleFormChange("repository", e.target.value); },
              },
              repoList.length === 0
                ? h("option", { value: "" }, "Loading…")
                : repoList.map(function (repo) {
                    var name = repo.full_name || repo.name || repo;
                    return h("option", { key: name, value: name }, name);
                  }),
            ),
          ),
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Provider"),
            h(
              "select",
              {
                style: inputStyle,
                value: form.provider,
                onChange: function (e) { handleFormChange("provider", e.target.value); },
              },
              providerList.length === 0
                ? h("option", { value: "claude_code_cli" }, "Claude Code CLI")
                : providerList.map(function (pid) {
                    var labels = {
                      claude_code_cli: "Claude Code CLI",
                      codex_cli: "Codex CLI",
                      gemini_cli: "Gemini CLI",
                      jules_api: "Jules API",
                      ollama: "Ollama",
                      cline: "Cline",
                    };
                    return h("option", { key: pid, value: pid }, labels[pid] || pid);
                  }),
            ),
          ),
          showModel
            ? h(
                "div",
                { style: rowStyle },
                h("label", { style: labelStyle }, "Model"),
                (function() {
                  var modelOpts = _PROVIDER_MODELS[form.provider];
                  if (modelOpts && modelOpts.length > 0) {
                    return h("select", {
                      style: inputStyle,
                      value: form.model,
                      onChange: function (e) { handleFormChange("model", e.target.value); },
                    },
                      modelOpts.map(function(m) {
                        return h("option", { key: m.value, value: m.value }, m.label);
                      })
                    );
                  }
                  return h("input", {
                    type: "text",
                    style: inputStyle,
                    value: form.model,
                    placeholder: "model name",
                    onChange: function (e) { handleFormChange("model", e.target.value); },
                  });
                })(),
              )
            : null,
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Branch ref"),
            h("input", {
              type: "text",
              style: inputStyle,
              value: form.ref,
              placeholder: "main",
              onChange: function (e) { handleFormChange("ref", e.target.value); },
            }),
          ),
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Prompt"),
            h("textarea", {
              rows: 4,
              style: Object.assign({}, inputStyle, { resize: "vertical", fontFamily: "inherit" }),
              value: form.prompt,
              placeholder: "Describe the task for the agent…",
              onChange: function (e) { handleFormChange("prompt", e.target.value); },
            }),
          ),
          error
            ? h(
                "div",
                {
                  style: {
                    fontSize: 12,
                    color: "var(--accent-red)",
                    marginBottom: 10,
                    padding: "6px 10px",
                    background: "rgba(248,81,73,0.1)",
                    borderRadius: 4,
                    border: "1px solid rgba(248,81,73,0.3)",
                  },
                },
                error,
              )
            : null,
          successMsg
            ? h(
                "div",
                {
                  style: {
                    fontSize: 12,
                    color: "var(--accent-green)",
                    marginBottom: 10,
                    padding: "6px 10px",
                    background: "rgba(63,185,80,0.1)",
                    borderRadius: 4,
                    border: "1px solid rgba(63,185,80,0.3)",
                  },
                },
                successMsg,
              )
            : null,
          h(
            "div",
            { style: { display: "flex", justifyContent: "flex-end", gap: 8 } },
            h(
              "button",
              { className: "btn", onClick: handleCancel, disabled: loading },
              "Cancel",
            ),
            h(
              "button",
              {
                className: "btn btn-blue",
                style: { background: "rgba(88,166,255,0.2)", fontWeight: 600 },
                onClick: handleDispatch,
                disabled: loading,
              },
              loading ? "Dispatching…" : "⚡ Dispatch",
            ),
          ),
        )
      : null,
  );
}

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
