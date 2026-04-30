import React from "react"
import { AgentDispatchPage } from "../pages/AgentDispatch"
import { QueueTab } from "../pages/Queue"
import { marked } from "marked"
import DOMPurify from "dompurify"

// @ts-nocheck
/* eslint-disable */

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

// Wrap global fetch to detect 401s and prompt login
var originalFetch = window.fetch;
window.fetch = async function(url, opts) {
  if (shouldBypassServiceWorkerCache(url)) {
    opts = Object.assign({}, opts || {}, { cache: "no-store" });
  }
  var resp = await originalFetch(url, opts);
  if (resp.status === 401 && !url.includes('/api/auth/me')) {
    console.warn("[auth] 401 Unauthorized from", url);
    // Show auth error overlay
    if (typeof window._showAuthError === "function") {
      window._showAuthError();
    } else {
      // Give them a chance to login
      var root = document.getElementById("root");
      if (root && root.querySelector && !root.querySelector('.auth-modal')) {
         var div = document.createElement("div");
         div.className = "auth-modal";
         div.style.position = "fixed";
         div.style.top = "0"; div.style.left = "0"; div.style.width = "100%"; div.style.height = "100%";
         div.style.background = "rgba(15,17,23,0.8)"; div.style.zIndex = "9999";
         div.style.display = "flex"; div.style.alignItems = "center"; div.style.justifyContent = "center";
         div.innerHTML = '<div style="background:var(--bg-secondary);padding:32px;border-radius:12px;border:1px solid var(--border);text-align:center"><h2 style="margin-bottom:16px;color:var(--accent-red)">Session Expired</h2><p style="margin-bottom:24px;color:var(--text-secondary)">Your session has expired. Please login again to continue.</p><a href="/api/auth/github" class="btn btn-blue" style="text-decoration:none">Login with GitHub</a></div>';
         document.body.appendChild(div);
      }
    }
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
          ? h("span", { className: "section-badge" }, p.badge)
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
  return h(
    "div",
    { className: "subtabs" + (p.className ? " " + p.className : "") },
    h(
      "div",
      { className: "subtabs-strip" },
      tabs.map(function (tab) {
        return h(
          "button",
          {
            key: tab.key,
            className: "subtab" + (activeKey === tab.key ? " active" : ""),
            disabled: tab.disabled || false,
            onClick: function () { if (!tab.disabled) handleChange(tab.key); },
          },
          tab.label,
          tab.badge != null
            ? h("span", { className: "subtab-badge" }, tab.badge)
            : null,
        );
      }),
    ),
    p.rightBadge ? h("div", { className: "subtabs-right" }, p.rightBadge) : null,
  );
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
    p.sub ? h("div", { className: "stat-sub" }, p.sub) : null,
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
    brick: "Brick",
    brickwindows: "Brick",
    bricklinux: "Brick",
    controltower: "ControlTower",
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
    role: "button",
    tabIndex: 0,
    "aria-sort": active
      ? dir === "desc"
        ? "descending"
        : "ascending"
      : "none",
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
  var driftState = React.useState(null);
  var driftInfo = driftState[0], setDriftInfo = driftState[1];
  React.useEffect(function () {
    fetch("/api/deployment/git-drift")
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
  return h(
    "div",
    null,
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
        sub:
          watchdog.detail ||
          watchdog.summary ||
          "Read-only keepalive checks",
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
                  "span",
                  {
                    className:
                      "runner-status-badge " +
                      (n.online ? "online" : "offline"),
                  },
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
              "button",
              {
                key: item.key,
                className:
                  "btn fleet-status-pill" +
                  (filter === item.key ? " btn-green" : ""),
                onClick: function () {
                  setFilter(item.key);
                },
                "aria-pressed": filter === item.key,
              },
              h("span", { className: "status-dot " + item.dot }),
              item.label,
              h("span", { className: "section-badge" }, item.count),
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

// ════════════════════════ ORGANIZATION TAB ════════════════════════
function OrgTab(p) {
  var repos = p.repos,
    loading = p.loading,
    stats = p.stats || {};
  var sr = React.useState("");
  var search = sr[0],
    setSearch = sr[1];
  var so = React.useState("updated");
  var sortBy = so[0],
    setSortBy = so[1];
  var filtered = repos.filter(function (r) {
    if (!search) return true;
    var q = search.toLowerCase();
    return (
      (r.name && r.name.toLowerCase().indexOf(q) >= 0) ||
      (r.description && r.description.toLowerCase().indexOf(q) >= 0) ||
      (r.language && r.language.toLowerCase().indexOf(q) >= 0)
    );
  });
  var sorted = filtered.slice().sort(function (a, b) {
    if (sortBy === "prs") return (b.open_prs || 0) - (a.open_prs || 0);
    if (sortBy === "issues")
      return (b.open_issues || 0) - (a.open_issues || 0);
    if (sortBy === "name")
      return (a.name || "").localeCompare(b.name || "");
    return (b.updated_at || "").localeCompare(a.updated_at || "");
  });
  var tPR = repos.reduce(function (s, r) {
    return s + (r.open_prs || 0);
  }, 0);
  var tI = repos.reduce(function (s, r) {
    return s + (r.open_issues || 0);
  }, 0);
  var wCI = repos.filter(function (r) {
    return r.last_ci_status;
  }).length;
  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Repositories",
        value: repos.length,
        sub: "in D-sorganization",
      }),
      h(Stat, {
        label: "Open PRs",
        value: tPR,
        color: tPR > 0 ? "var(--accent-blue)" : "inherit",
        sub: "across all repos",
      }),
      h(Stat, {
        label: "Open Issues",
        value: stats.org_open_issues != null ? stats.org_open_issues : tI,
        color:
          (stats.org_open_issues || tI) > 0
            ? "var(--accent-orange)"
            : "inherit",
        sub: "across all repos",
      }),
      h(Stat, {
        label: "CI/CD Active",
        value: wCI,
        sub: "repos with workflows",
      }),
    ),
    h(
      "div",
      { className: "toolbar" },
      h("input", {
        className: "search-bar",
        "aria-label": "Search repositories",
        placeholder: "Search repos...",
        value: search,
        onChange: function (e) {
          setSearch(e.target.value);
        },
      }),
      h(
        "div",
        { className: "toolbar-right" },
        h(
          "span",
          { style: { color: "var(--text-muted)", fontSize: 12 } },
          "Sort:",
        ),
        h(
          "button",
          {
            className: "btn" + (sortBy === "updated" ? " btn-green" : ""),
            onClick: function () {
              setSortBy("updated");
            },
          },
          "Recent",
        ),
        h(
          "button",
          {
            className: "btn" + (sortBy === "prs" ? " btn-green" : ""),
            onClick: function () {
              setSortBy("prs");
            },
          },
          "PRs",
        ),
        h(
          "button",
          {
            className: "btn" + (sortBy === "issues" ? " btn-green" : ""),
            onClick: function () {
              setSortBy("issues");
            },
          },
          "Issues",
        ),
        h(
          "button",
          {
            className: "btn" + (sortBy === "name" ? " btn-green" : ""),
            onClick: function () {
              setSortBy("name");
            },
          },
          "Name",
        ),
        loading ? h("span", { className: "spinner" }) : null,
      ),
    ),
    h(
      "div",
      { className: "section", style: { overflowX: "auto" } },
      h(
        "table",
        { className: "data-table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", null, "Repository"),
            h("th", null, "Language"),
            h("th", { style: { textAlign: "center" } }, "PRs"),
            h("th", { style: { textAlign: "center" } }, "Issues"),
            h("th", null, "CI/CD"),
            h("th", null, "Updated"),
          ),
        ),
        h(
          "tbody",
          null,
          sorted.length > 0
            ? sorted.map(function (r) {
                var ci = r.last_ci_conclusion || r.last_ci_status;
                return h(
                  "tr",
                  { key: r.name },
                  h(
                    "td",
                    null,
                    h(
                      "div",
                      { className: "repo-name-cell" },
                      h(
                        "div",
                        {
                          style: {
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                          },
                        },
                        h(
                          "a",
                          {
                            className: "repo-name-link",
                            href: r.url,
                            target: "_blank",
                            rel: "noopener",
                          },
                          r.name,
                        ),
                        r.private
                          ? h(
                              "span",
                              { className: "visibility-badge" },
                              "private",
                            )
                          : h(
                              "span",
                              {
                                className: "visibility-badge",
                                style: {
                                  borderColor: "var(--accent-green)",
                                  color: "var(--accent-green)",
                                },
                              },
                              "public",
                            ),
                      ),
                      r.description
                        ? h(
                            "div",
                            { className: "repo-desc" },
                            r.description,
                          )
                        : null,
                    ),
                  ),
                  h(
                    "td",
                    null,
                    r.language
                      ? h(
                          "span",
                          {
                            style: {
                              display: "flex",
                              alignItems: "center",
                              gap: 6,
                            },
                          },
                          h("span", {
                            className: "lang-dot",
                            style: {
                              background:
                                LANG_COLORS[r.language] || "#8b949e",
                            },
                          }),
                          r.language,
                        )
                      : h(
                          "span",
                          { style: { color: "var(--text-muted)" } },
                          "-",
                        ),
                  ),
                  h(
                    "td",
                    { style: { textAlign: "center" } },
                    h(
                      "span",
                      {
                        className:
                          "count-badge " +
                          (r.open_prs > 0 ? "has-items" : "zero"),
                      },
                      I.gitPR(14),
                      r.open_prs || 0,
                    ),
                  ),
                  h(
                    "td",
                    { style: { textAlign: "center" } },
                    h(
                      "span",
                      {
                        className:
                          "count-badge " +
                          (r.open_issues > 0 ? "has-items" : "zero"),
                      },
                      I.issue(14),
                      r.open_issues || 0,
                    ),
                  ),
                  h(
                    "td",
                    null,
                    ci
                      ? h(
                          "a",
                          {
                            href: r.last_ci_run_url,
                            target: "_blank",
                            rel: "noopener",
                            style: { textDecoration: "none" },
                          },
                          h(
                            "span",
                            { className: "conclusion-badge " + ci },
                            ci,
                          ),
                        )
                      : h(
                          "span",
                          {
                            style: {
                              color: "var(--text-muted)",
                              fontSize: 12,
                            },
                          },
                          "No CI",
                        ),
                  ),
                  h(
                    "td",
                    { style: { color: "var(--text-muted)" } },
                    timeAgo(r.updated_at),
                  ),
                );
              })
            : h(
                "tr",
                null,
                h(
                  "td",
                  {
                    colSpan: 6,
                    style: {
                      textAlign: "center",
                      padding: 40,
                      color: "var(--text-muted)",
                    },
                  },
                  loading ? "Loading..." : "No repos found",
                ),
              ),
        ),
      ),
    ),
  );
}

// ════════════════════════ TESTS TAB ════════════════════════
function TestsTab(p) {
  var testRepos = p.testRepos;
  var loading = p.loading;
  var ciResults = p.ciResults || [];

  // CI rerun state per repo
  var rrs = React.useState({});
  var rerunState = rrs[0],
    setRerunState = rrs[1];

  function rerunFailed(repo, runId) {
    setRerunState(function (prev) {
      var n = Object.assign({}, prev);
      n[repo] = "running";
      return n;
    });
    fetch("/api/tests/rerun", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ repo: repo, run_id: runId }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("rerun failed");
        return r.json();
      })
      .then(function () {
        setRerunState(function (prev) {
          var n = Object.assign({}, prev);
          n[repo] = "triggered";
          return n;
        });
      })
      .catch(function () {
        setRerunState(function (prev) {
          var n = Object.assign({}, prev);
          n[repo] = "error";
          return n;
        });
      });
  }

  var conclusionColor = {
    success: "var(--accent-green)",
    failure: "var(--accent-red)",
    cancelled: "var(--text-secondary)",
    skipped: "var(--text-secondary)",
    in_progress: "var(--accent-yellow)",
    queued: "var(--accent-yellow)",
  };

  var ciSection = h(
    "div",
    { style: { marginBottom: 24 } },
    h(
      "div",
      {
        style: {
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
        },
      },
      h(
        "span",
        {
          style: {
            fontWeight: 700,
            fontSize: 16,
            color: "var(--text-primary)",
          },
        },
        I.activity(16),
        " CI Tests — Fleet Repos",
      ),
    ),
    ciResults.length === 0
      ? h(
          "div",
          {
            style: {
              color: "var(--text-secondary)",
              fontSize: 13,
              padding: "12px 0",
            },
          },
          "Loading CI results…",
        )
      : h(
          "div",
          { style: { overflowX: "auto" } },
          h(
            "table",
            { className: "data-table" },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h("th", null, "Repo"),
                h("th", null, "Status"),
                h("th", null, "Branch"),
                h("th", null, "Run #"),
                h("th", null, "Updated"),
                h("th", null, "Actions"),
              ),
            ),
            h(
              "tbody",
              null,
              ciResults.map(function (r) {
                var concl = r.conclusion || r.status || "unknown";
                var color = conclusionColor[concl] || "var(--text-secondary)";
                var rerunSt = rerunState[r.repo];
                var canRerun =
                  r.run_id &&
                  (r.conclusion === "failure" || r.conclusion === "cancelled");
                return h(
                  "tr",
                  { key: r.repo },
                  h(
                    "td",
                    null,
                    r.html_url
                      ? h(
                          "a",
                          {
                            href: r.html_url,
                            target: "_blank",
                            style: { color: "var(--accent-blue)" },
                          },
                          r.repo,
                        )
                      : r.repo,
                  ),
                  h(
                    "td",
                    null,
                    h(
                      "span",
                      {
                        className: "conclusion-badge " + concl,
                        style: { color: color },
                      },
                      concl,
                    ),
                  ),
                  h("td", null, r.head_branch || "main"),
                  h("td", null, r.run_number ? "#" + r.run_number : "—"),
                  h(
                    "td",
                    null,
                    r.updated_at
                      ? new Date(r.updated_at).toLocaleString()
                      : "—",
                  ),
                  h(
                    "td",
                    null,
                    canRerun
                      ? h(
                          "button",
                          {
                            className: "btn btn-sm btn-blue",
                            disabled: rerunSt === "running",
                            onClick: function () {
                              rerunFailed(r.repo, r.run_id);
                            },
                          },
                          rerunSt === "running"
                            ? h("span", { className: "spinner" })
                            : I.play(12),
                          rerunSt === "triggered"
                            ? " Triggered"
                            : " Re-run Failed",
                        )
                      : r.run_id
                        ? h(
                            "a",
                            {
                              href: r.html_url,
                              target: "_blank",
                              className: "btn btn-sm",
                              style: { textDecoration: "none" },
                            },
                            "View",
                          )
                        : "—",
                  ),
                );
              }),
            ),
          ),
        ),
  );
  var ds = React.useState({});
  var dispatchState = ds[0],
    setDispatchState = ds[1];

  function dispatch(repoName, method, pyVer, ref) {
    var key = repoName + "-" + method;
    setDispatchState(function (prev) {
      var n = {};
      for (var k in prev) n[k] = prev[k];
      n[key] = { status: "dispatching", output: "" };
      return n;
    });
    var url =
      method === "docker"
        ? "/api/heavy-tests/docker"
        : "/api/heavy-tests/dispatch";
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({
        repo: repoName,
        python_version: pyVer,
        ref: ref || "main",
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        setDispatchState(function (prev) {
          var n = {};
          for (var k in prev) n[k] = prev[k];
          n[key] = {
            status: data.status || "done",
            output: JSON.stringify(data, null, 2),
          };
          return n;
        });
      })
      .catch(function (err) {
        setDispatchState(function (prev) {
          var n = {};
          for (var k in prev) n[k] = prev[k];
          n[key] = { status: "error", output: err.message };
          return n;
        });
      });
  }

  return h(
    "div",
    null,
    ciSection,
    h(
      "div",
      {
        style: {
          borderTop: "1px solid var(--border)",
          margin: "0 0 20px 0",
          paddingTop: 20,
        },
      },
      h(
        "div",
        {
          style: {
            fontWeight: 700,
            fontSize: 16,
            color: "var(--text-primary)",
            marginBottom: 12,
          },
        },
        I.flask(16),
        " Integration Tests — Heavy Test Suite",
      ),
    ),
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Heavy Test Repos",
        value: testRepos.length,
        sub: "with workflow_dispatch",
      }),
      h(Stat, {
        label: "Primary",
        value: "UpstreamDrift",
        sub: "MuJoCo, Drake, Pinocchio",
        color: "var(--accent-purple)",
      }),
      h(Stat, {
        label: "Methods",
        value: "2",
        sub: "GitHub Actions + Docker",
      }),
      h(Stat, {
        label: "Schedule",
        value: "Weekly",
        sub: "Sun 02:00 UTC",
      }),
    ),
    testRepos.map(function (repo) {
      var pyRef = React.useState(repo.default_python);
      var pyVer = pyRef[0],
        setPyVer = pyRef[1];
      var brRef = React.useState("main");
      var branch = brRef[0],
        setBranch = brRef[1];
      var ghKey = repo.name + "-github";
      var dkKey = repo.name + "-docker";
      var ghState = dispatchState[ghKey] || {};
      var dkState = dispatchState[dkKey] || {};

      return h(
        "div",
        { className: "test-card", key: repo.name },
        h(
          "div",
          { className: "test-card-header" },
          h(
            "div",
            null,
            h(
              "div",
              { className: "test-card-title" },
              I.flask(20),
              " ",
              repo.name,
            ),
            h("div", { className: "test-card-desc" }, repo.description),
          ),
        ),
        h(
          "div",
          { className: "form-row" },
          h("span", { className: "form-label" }, "Python Version:"),
          h(
            "select",
            {
              className: "form-select",
              value: pyVer,
              onChange: function (e) {
                setPyVer(e.target.value);
              },
            },
            repo.python_versions.map(function (v) {
              return h("option", { key: v, value: v }, v);
            }),
          ),
        ),
        h(
          "div",
          { className: "form-row" },
          h("span", { className: "form-label" }, "Branch / Ref:"),
          h("input", {
            className: "form-input",
            value: branch,
            onChange: function (e) {
              setBranch(e.target.value);
            },
            placeholder: "main",
          }),
        ),
        h(
          "div",
          { className: "dispatch-actions" },
          h(
            "button",
            {
              className: "btn btn-lg btn-blue",
              disabled: ghState.status === "dispatching",
              onClick: function () {
                dispatch(repo.name, "github", pyVer, branch);
              },
            },
            ghState.status === "dispatching"
              ? h("span", { className: "spinner" })
              : I.play(14),
            " Run via GitHub Actions",
          ),
          h(
            "button",
            {
              className: "btn btn-lg btn-purple",
              disabled: dkState.status === "dispatching",
              onClick: function () {
                dispatch(repo.name, "docker", pyVer, branch);
              },
            },
            dkState.status === "dispatching"
              ? h("span", { className: "spinner" })
              : I.docker(14),
            " Run in Docker (Local)",
          ),
        ),
        ghState.output
          ? h(
              "div",
              null,
              h(
                "div",
                {
                  style: {
                    fontSize: 12,
                    color: "var(--text-secondary)",
                    marginTop: 12,
                  },
                },
                "GitHub Actions: ",
                h(
                  "span",
                  {
                    className:
                      "conclusion-badge " +
                      (ghState.status === "dispatched"
                        ? "success"
                        : ghState.status === "error"
                          ? "failure"
                          : "in_progress"),
                  },
                  ghState.status,
                ),
              ),
              h("div", { className: "output-box" }, ghState.output),
            )
          : null,
        dkState.output
          ? h(
              "div",
              null,
              h(
                "div",
                {
                  style: {
                    fontSize: 12,
                    color: "var(--text-secondary)",
                    marginTop: 12,
                  },
                },
                "Docker: ",
                h(
                  "span",
                  {
                    className:
                      "conclusion-badge " +
                      (dkState.status === "completed"
                        ? "success"
                        : dkState.status === "error" ||
                            dkState.status === "failed"
                          ? "failure"
                          : "in_progress"),
                  },
                  dkState.status,
                ),
              ),
              h("div", { className: "output-box" }, dkState.output),
            )
          : null,

        // Recent runs
        repo.recent_runs && repo.recent_runs.length > 0
          ? h(
              Collapse,
              {
                title: "Recent Heavy Test Runs",
                icon: I.activity(14),
                badge: repo.recent_runs.length + " runs",
                defaultOpen: false,
              },
              h(
                "div",
                { style: { overflowX: "auto" } },
                h(
                  "table",
                  { className: "data-table" },
                  h(
                    "thead",
                    null,
                    h(
                      "tr",
                      null,
                      h("th", null, "#"),
                      h("th", null, "Status"),
                      h("th", null, "Branch"),
                      h("th", null, "Triggered By"),
                      h("th", null, "When"),
                      h("th", null, "Link"),
                    ),
                  ),
                  h(
                    "tbody",
                    null,
                    repo.recent_runs.map(function (run) {
                      var c = run.conclusion || run.status;
                      return h(
                        "tr",
                        { key: run.id },
                        h("td", null, run.run_number),
                        h(
                          "td",
                          null,
                          h(
                            "span",
                            { className: "conclusion-badge " + c },
                            c,
                          ),
                        ),
                        h("td", null, run.head_branch),
                        h("td", null, run.triggering_actor || "-"),
                        h(
                          "td",
                          { style: { color: "var(--text-muted)" } },
                          timeAgo(run.updated_at),
                        ),
                        h(
                          "td",
                          null,
                          h(
                            "a",
                            {
                              href: run.html_url,
                              target: "_blank",
                              rel: "noopener",
                              style: {
                                color: "var(--accent-blue)",
                                textDecoration: "none",
                                fontSize: 12,
                              },
                            },
                            "View",
                          ),
                        ),
                      );
                    }),
                  ),
                ),
              ),
            )
          : null,
      );
    }),
  );
}

// ════════════════════════ DAILY REPORTS TAB ════════════════════════
// ════════════════════════ STATS TAB (workflow durations) ════════════════════════
function StatsTab(p) {
  var ds = React.useState({ rows: [], window_days: 14 });
  var data = ds[0],
    setData = ds[1];
  var ls = React.useState(false);
  var loading = ls[0],
    setLoading = ls[1];
  var tss = React.useState({ series: [] });
  var timeseries = tss[0],
    setTimeseries = tss[1];
  var grp = React.useState("workflow");
  var groupBy = grp[0],
    setGroupBy = grp[1];
  var dys = React.useState(14);
  var days = dys[0],
    setDays = dys[1];
  var sel = React.useState(null);
  var selected = sel[0],
    setSelected = sel[1];

  function refresh() {
    setLoading(true);
    var wfParams = new URLSearchParams({ days: days, group_by: groupBy });
    fetch("/api/stats/workflows?" + wfParams)
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setData(d || { rows: [] });
        setLoading(false);
      })
      .catch(function () {
        setLoading(false);
      });
  }
  function refreshTimeseries(row) {
    if (!row) {
      setTimeseries({ series: [] });
      return;
    }
    var tsParamObj = { days: Math.max(days, 30), bucket_hours: 24 };
    if (groupBy === "workflow") {
      tsParamObj.repo = row.repo;
      tsParamObj.workflow_name = row.workflow_name;
    } else {
      tsParamObj.repo = row.repo;
    }
    fetch("/api/stats/workflows/timeseries?" + new URLSearchParams(tsParamObj))
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setTimeseries(d || { series: [] });
      });
  }
  function collectNow() {
    setLoading(true);
    fetch("/api/stats/workflows/collect", { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) {
        return r.json();
      })
      .then(function () {
        refresh();
      });
  }
  React.useEffect(
    function () {
      refresh();
    },
    [days, groupBy],
  );
  React.useEffect(
    function () {
      refreshTimeseries(selected);
    },
    [selected],
  );

  function fmtDur(s) {
    if (s === null || s === undefined) return "-";
    if (s < 60) return Math.round(s) + "s";
    if (s < 3600) return (s / 60).toFixed(1) + "m";
    return (s / 3600).toFixed(1) + "h";
  }

  var rows = data.rows || [];
  var totalRuns = rows.reduce(function (a, r) {
    return a + (r.count || 0);
  }, 0);
  var avgP50 = rows.length
    ? rows.reduce(function (a, r) {
        return a + (r.p50_duration || 0);
      }, 0) / rows.length
    : 0;

  // Mini sparkline: draw p50 and p95 as SVG polylines
  function drawSparkline(series, height) {
    if (!series || series.length < 2)
      return h(
        "div",
        {
          style: {
            color: "var(--text-muted)",
            fontSize: 12,
            padding: 20,
            textAlign: "center",
          },
        },
        "Not enough data yet — collector runs every 10 minutes.",
      );
    var w = 800,
      h0 = height || 180;
    var maxD =
      Math.max.apply(
        null,
        series.map(function (s) {
          return s.p95_duration || 0;
        }),
      ) || 1;
    function xy(i, v) {
      return [
        (i / (series.length - 1)) * w,
        h0 - ((v || 0) / maxD) * (h0 - 20) - 5,
      ];
    }
    var p50 = series
      .map(function (s, i) {
        return xy(i, s.p50_duration).join(",");
      })
      .join(" ");
    var p95 = series
      .map(function (s, i) {
        return xy(i, s.p95_duration).join(",");
      })
      .join(" ");
    return h(
      "div",
      { style: { position: "relative" } },
      h(
        "svg",
        {
          viewBox: "0 0 " + w + " " + h0,
          style: {
            width: "100%",
            height: h0,
            background: "rgba(63,185,80,0.02)",
            borderRadius: 8,
          },
        },
        h("polyline", {
          points: p95,
          fill: "none",
          stroke: "var(--accent-red)",
          strokeWidth: 2,
          opacity: 0.7,
        }),
        h("polyline", {
          points: p50,
          fill: "none",
          stroke: "var(--accent-green)",
          strokeWidth: 2,
        }),
        series.map(function (s, i) {
          var c = xy(i, s.p50_duration);
          return h("circle", {
            key: i,
            cx: c[0],
            cy: c[1],
            r: 2,
            fill: "var(--accent-green)",
          });
        }),
      ),
      h(
        "div",
        {
          style: {
            display: "flex",
            justifyContent: "space-between",
            fontSize: 11,
            color: "var(--text-muted)",
            marginTop: 4,
          },
        },
        h("span", null, series[0].t.slice(0, 10)),
        h(
          "span",
          null,
          h("span", { style: { color: "var(--accent-green)" } }, "p50 "),
          h(
            "span",
            { style: { color: "var(--accent-red)", marginLeft: 8 } },
            "p95",
          ),
        ),
        h("span", null, series[series.length - 1].t.slice(0, 10)),
      ),
    );
  }

  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Tracked workflows",
        value: rows.length,
        sub: (data.window_days || days) + "-day window",
      }),
      h(Stat, { label: "Completed runs", value: totalRuns }),
      h(Stat, { label: "Mean P50 duration", value: fmtDur(avgP50) }),
      h(Stat, {
        label: "Auto-refresh",
        value: "on demand",
        sub: "collector polls every 10m",
      }),
    ),
    h(
      "div",
      {
        style: {
          display: "flex",
          gap: 8,
          alignItems: "center",
          marginBottom: 12,
          flexWrap: "wrap",
        },
      },
      h(
        "label",
        { style: { fontSize: 13, color: "var(--text-secondary)" } },
        "Group by:",
      ),
      h(
        "select",
        {
          value: groupBy,
          onChange: function (e) {
            setGroupBy(e.target.value);
            setSelected(null);
          },
          style: {
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "4px 8px",
          },
        },
        h("option", { value: "workflow" }, "Workflow (repo + name)"),
        h("option", { value: "repo" }, "Repository"),
      ),
      h(
        "label",
        {
          style: {
            fontSize: 13,
            color: "var(--text-secondary)",
            marginLeft: 12,
          },
        },
        "Window:",
      ),
      h(
        "select",
        {
          value: days,
          onChange: function (e) {
            setDays(parseInt(e.target.value));
            setSelected(null);
          },
          style: {
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "4px 8px",
          },
        },
        [7, 14, 30, 60, 90].map(function (d) {
          return h("option", { key: d, value: d }, d + " days");
        }),
      ),
      h(
        "button",
        { className: "btn", onClick: refresh, disabled: loading },
        loading ? "…" : "Refresh",
      ),
      h(
        "button",
        {
          className: "btn",
          onClick: collectNow,
          title:
            "Force collector to run now (otherwise runs every 10 min)",
        },
        "Collect now",
      ),
    ),
    selected
      ? h(
          "div",
          { className: "card", style: { marginBottom: 12 } },
          h(
            "div",
            {
              style: {
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
              },
            },
            h(
              "div",
              null,
              h(
                "div",
                { style: { fontWeight: 600, fontSize: 15 } },
                groupBy === "workflow"
                  ? selected.repo + " · " + selected.workflow_name
                  : selected.repo,
              ),
              h(
                "div",
                { style: { fontSize: 12, color: "var(--text-muted)" } },
                "P50 duration over time — hover the chart for bucketed detail",
              ),
            ),
            h(
              "button",
              {
                className: "btn",
                onClick: function () {
                  setSelected(null);
                },
              },
              "Close",
            ),
          ),
          drawSparkline(timeseries.series, 200),
        )
      : null,
    loading && rows.length === 0
      ? h(
          "div",
          {
            style: {
              padding: 40,
              textAlign: "center",
              color: "var(--text-muted)",
            },
          },
          "Loading…",
        )
      : rows.length === 0
        ? h(
            "div",
            {
              style: {
                padding: 40,
                textAlign: "center",
                color: "var(--text-muted)",
              },
            },
            "No data yet. Click ‘Collect now’ to pull recent runs from GitHub, then wait ~1 min for the first pass.",
          )
        : h(
            "div",
            { className: "card" },
            h(
              "table",
              { className: "run-table", style: { width: "100%" } },
              h(
                "thead",
                null,
                h(
                  "tr",
                  null,
                  h("th", null, "Repo"),
                  groupBy === "workflow"
                    ? h("th", null, "Workflow")
                    : null,
                  h("th", null, "Runs"),
                  h("th", null, "Success %"),
                  h("th", null, "P50 dur"),
                  h("th", null, "P95 dur"),
                  h("th", null, "P50 queued"),
                  h("th", null, "P95 queued"),
                  h("th", null, ""),
                ),
              ),
              h(
                "tbody",
                null,
                rows.map(function (r, i) {
                  var isSel =
                    selected &&
                    selected.repo === r.repo &&
                    selected.workflow_name === r.workflow_name;
                  return h(
                    "tr",
                    {
                      key: i,
                      style: isSel
                        ? { background: "rgba(88,166,255,0.06)" }
                        : {},
                    },
                    h("td", null, r.repo),
                    groupBy === "workflow"
                      ? h(
                          "td",
                          {
                            style: {
                              maxWidth: 300,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            },
                            title: r.workflow_name,
                          },
                          r.workflow_name,
                        )
                      : null,
                    h("td", null, r.count),
                    h(
                      "td",
                      {
                        style: {
                          color:
                            r.success_rate >= 90
                              ? "var(--accent-green)"
                              : r.success_rate >= 70
                                ? "var(--accent-yellow)"
                                : "var(--accent-red)",
                        },
                      },
                      r.success_rate + "%",
                    ),
                    h("td", null, fmtDur(r.p50_duration)),
                    h("td", null, fmtDur(r.p95_duration)),
                    h("td", null, fmtDur(r.p50_queued)),
                    h("td", null, fmtDur(r.p95_queued)),
                    h(
                      "td",
                      null,
                      h(
                        "button",
                        {
                          className: "btn",
                          onClick: function () {
                            setSelected(r);
                          },
                        },
                        "Trend",
                      ),
                    ),
                  );
                }),
              ),
            ),
          ),
  );
}

function ReportsTab(p) {
  var reports = p.reports;
  var loading = p.loading;
  var selRef = React.useState(null);
  var selected = selRef[0],
    setSelected = selRef[1];
  var contentRef = React.useState(null);
  var content = contentRef[0],
    setContent = contentRef[1];
  var metricsRef = React.useState({});
  var metrics = metricsRef[0],
    setMetrics = metricsRef[1];
  var loadingReport = React.useState(false);
  var rl = loadingReport[0],
    setRl = loadingReport[1];

  function loadReport(date) {
    setSelected(date);
    setRl(true);
    fetch("/api/reports/" + date)
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        setContent(data.content || "");
        setMetrics(data.metrics || {});
        setRl(false);
      })
      .catch(function () {
        setContent("Failed to load report.");
        setRl(false);
      });
  }

  // Auto-load latest on mount
  React.useEffect(
    function () {
      if (reports.length > 0 && !selected) {
        loadReport(reports[0].date);
      }
    },
    [reports.length],
  );

  function renderMarkdown(md) {
    if (typeof marked !== "undefined" && marked.parse) {
      var raw = marked.parse(md);
      // Sanitize via DOMPurify to prevent XSS (issue #7)
      var clean =
        typeof DOMPurify !== "undefined"
          ? DOMPurify.sanitize(raw)
          : raw;
      return { __html: clean };
    }
    // Fallback: just show raw text (HTML-escaped, inherently safe)
    return { __html: "<pre>" + md.replace(/</g, "&lt;") + "</pre>" };
  }

  // Extract key metrics for stat cards
  var prsMerged = metrics["PRs Merged (24h)"] || {};
  var issuesOpen = metrics["Issues Currently Open"] || {};
  var score =
    metrics["Fleet Average Score (20 repos)"] ||
    metrics["Fleet Average Score"] ||
    {};
  var ciIssue = metrics["PRs Merged with Failing CI"] || {};

  return h(
    "div",
    null,
    reports.length > 0
      ? h(
          "div",
          { className: "stat-row" },
          h(Stat, {
            label: "Latest Report",
            value: reports[0].date,
            sub: reports[0].size_kb + " KB",
          }),
          h(Stat, {
            label: "PRs Merged",
            value: prsMerged.value || "-",
            color: "var(--accent-blue)",
            sub: prsMerged.delta || "",
          }),
          h(Stat, {
            label: "Issues Open",
            value: issuesOpen.value || "-",
            color: "var(--accent-orange)",
            sub: issuesOpen.delta || "",
          }),
          h(Stat, {
            label: "Fleet Score",
            value: score.value || "-",
            color: "var(--accent-green)",
            sub: score.delta || "",
          }),
          ciIssue.value
            ? h(Stat, {
                label: "Failing CI Merges",
                value: ciIssue.value,
                color: "var(--accent-red)",
                sub: ciIssue.delta || "",
              })
            : null,
        )
      : null,

    h(
      "div",
      { className: "reports-shell" },
      // Report list sidebar
      h(
        "div",
        { className: "reports-sidebar" },
        h(
          "div",
          { className: "section" },
          h(
            "div",
            {
              style: {
                padding: "12px 16px",
                fontWeight: 600,
                fontSize: 14,
                borderBottom: "1px solid var(--border)",
              },
            },
            "Reports ",
            loading ? h("span", { className: "spinner" }) : null,
          ),
          h(
            "ul",
            { className: "report-list" },
            reports.map(function (r) {
              return h(
                "li",
                {
                  key: r.date,
                  className:
                    "report-item" +
                    (selected === r.date ? " active" : ""),
                  onClick: function () {
                    loadReport(r.date);
                  },
                },
                h(
                  "div",
                  null,
                  h("div", { className: "report-date" }, r.date),
                  h(
                    "div",
                    { className: "report-meta" },
                    r.size_kb + " KB",
                    r.has_chart ? " \u00B7 \uD83D\uDCC8" : "",
                  ),
                ),
              );
            }),
            reports.length === 0
              ? h(
                  "li",
                  {
                    style: {
                      padding: 20,
                      textAlign: "center",
                      color: "var(--text-muted)",
                    },
                  },
                  "No reports found",
                )
              : null,
          ),
        ),
      ),

      // Report content
      h(
        "div",
        { className: "reports-reader" },
        selected
          ? h(
              "div",
              null,
              h(
                "div",
                {
                  style: {
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 12,
                    marginBottom: 10,
                  },
                },
                h(
                  "span",
                  {
                    className: "section-badge",
                    style: { maxWidth: "70%", overflow: "hidden", textOverflow: "ellipsis" },
                  },
                  selected,
                ),
                h(
                  "a",
                  {
                    className: "report-open-raw",
                    href: "/api/reports/" + selected,
                    target: "_blank",
                    rel: "noopener noreferrer",
                  },
                  "Open raw",
                ),
              ),
              // Chart image if available
              reports.filter(function (r) {
                return r.date === selected && r.has_chart;
              }).length > 0
                ? h(
                    "div",
                    { style: { marginBottom: 16 } },
                    h("img", {
                      src: "/api/reports/" + selected + "/chart",
                      alt: "Assessment Scores",
                      style: {
                        maxWidth: "100%",
                        borderRadius: 8,
                        border: "1px solid var(--border)",
                      },
                    }),
                  )
                : null,
              rl
                ? h(
                    "div",
                    { style: { textAlign: "center", padding: 40 } },
                    h("span", { className: "spinner" }),
                  )
                : h("div", {
                    className: "report-content",
                    dangerouslySetInnerHTML: renderMarkdown(
                      content || "",
                    ),
                  }),
            )
          : h(
              "div",
              {
                style: {
                  padding: 40,
                  textAlign: "center",
                  color: "var(--text-muted)",
                },
              },
              "Select a report from the list",
            ),
      ),
    ),
  );
}

// ════════════════════════ MACHINES TAB ════════════════════════
function MachineCard(p) {
  var n = p.node;
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
    Brick: "var(--accent-green)",
    DeskComputer: "var(--accent-blue)",
    Oglaptop: "var(--accent-orange)",
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
      h(SystemResourcesPanel, { system: sys }),
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

function DeploymentTab(p) {
  var data = p.data || {};
  var loading = p.loading;
  var onRefresh = p.onRefresh || function () {};
  var onOpenFleet = p.onOpenFleet || function () {};
  var previewState = React.useState(null);
  var preview = previewState[0],
    setPreview = previewState[1];
  var rollout = data.rollout_state || {};
  var machines = (data.machines || []).slice().sort(function (a, b) {
    var priority = {
      dirty: 0,
      offline: 1,
      drifted: 2,
      degraded: 3,
      unknown: 4,
      steady: 5,
    };
    var ap =
      priority[a.rollout_state] != null ? priority[a.rollout_state] : 9;
    var bp =
      priority[b.rollout_state] != null ? priority[b.rollout_state] : 9;
    if (ap !== bp) return ap - bp;
    return (a.display_name || a.name || "").localeCompare(
      b.display_name || b.name || "",
    );
  });

  function renderVersion(value) {
    return value && value !== "unknown" ? value : "unknown";
  }

  function refreshAfterAction() {
    onRefresh();
    setTimeout(onRefresh, 1500);
  }

  function previewUpdate(machine) {
    if (!machine || !machine.name) return;
    setPreview({
      loading: true,
      machine: machine,
      title: "Loading dry-run preview...",
    });
    fetch("/api/deployment/update-signal", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({
        node: machine.name,
        reason: "dashboard-ui",
        dry_run: true,
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setPreview({
          loading: false,
          machine: machine,
          preview: d.preview || null,
          drift: d.drift || null,
          dryRun: true,
        });
      })
      .catch(function () {
        setPreview({
          loading: false,
          machine: machine,
          error: "Dry-run preview failed.",
        });
      });
  }

  function confirmUpdate() {
    if (!preview || !preview.machine) return;
    var machine = preview.machine;
    setPreview(
      Object.assign({}, preview, {
        loading: true,
        confirming: true,
      }),
    );
    fetch("/api/deployment/update-signal", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({
        node: machine.name,
        reason: "dashboard-ui",
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setPreview({
          loading: false,
          machine: machine,
          result: d.event || null,
          drift: d.drift || null,
          confirmed: true,
        });
        refreshAfterAction();
      })
      .catch(function () {
        setPreview({
          loading: false,
          machine: machine,
          error: "Update signal failed.",
        });
      });
  }

  var attentionCount = rollout.machines_attention || 0;
  var onlineCount = rollout.machines_online || 0;
  var totalCount = rollout.machines_total || machines.length;
  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Rollout",
        value: rollout.status || "unknown",
        color:
          rollout.status === "stable"
            ? "var(--accent-green)"
            : rollout.status === "blocked"
              ? "var(--accent-red)"
              : rollout.status === "degraded"
                ? "var(--accent-yellow)"
                : "inherit",
        sub: rollout.summary || "Deployment state across the fleet",
      }),
      h(Stat, {
        label: "Attention",
        value: attentionCount,
        color: attentionCount > 0 ? "var(--accent-yellow)" : "inherit",
        sub: "offline, drifting, dirty, or unknown machines",
      }),
      h(Stat, {
        label: "Online",
        value: onlineCount + "/" + totalCount,
        color:
          totalCount > 0 && onlineCount === totalCount
            ? "var(--accent-green)"
            : "var(--accent-yellow)",
        sub: "machines reporting dashboard telemetry",
      }),
      h(Stat, {
        label: "Expected",
        value: renderVersion(data.expected_version),
        sub: "hub VERSION target",
      }),
      h(Stat, {
        label: "Current",
        value: renderVersion((data.drift || {}).current),
        sub: (data.drift || {}).message || "current deployment",
      }),
    ),
    h(
      "div",
      { className: "deployment-note" },
      h("span", null, "Deployment state for"),
      h(
        "code",
        null,
        renderVersion(
          (data.drift || {}).expected || data.expected_version,
        ),
      ),
      h(
        "button",
        {
          className: "btn",
          style: { padding: "0 8px", fontSize: 11, height: 22 },
          onClick: onOpenFleet,
        },
        "Fleet overview",
      ),
      loading ? h("span", null, "Loading...") : null,
    ),
    preview
      ? h(
          "div",
          { className: "deployment-preview" },
          h(
            "div",
            {
              style: {
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                flexWrap: "wrap",
              },
            },
            h(
              "strong",
              null,
              preview.loading
                ? "Building dry-run preview"
                : preview.confirmed
                  ? "Update signal sent"
                  : preview.error
                    ? "Preview error"
                    : "Dry-run preview",
            ),
            h(
              "div",
              { style: { display: "flex", gap: 8, flexWrap: "wrap" } },
              preview.preview && !preview.confirmed && !preview.error
                ? h(
                    "button",
                    { className: "btn", onClick: confirmUpdate },
                    "Confirm update",
                  )
                : null,
              h(
                "button",
                {
                  className: "btn",
                  onClick: function () {
                    setPreview(null);
                  },
                },
                "Clear",
              ),
            ),
          ),
          h(
            "pre",
            null,
            preview.error
              ? preview.error
              : JSON.stringify(
                  preview.preview ||
                    preview.drift ||
                    preview.result ||
                    {},
                  null,
                  2,
                ),
          ),
        )
      : null,
    h(
      "div",
      { className: "deployment-state-machine-list" },
      machines.map(function (machine) {
        var drift = machine.drift_status || {};
        var statusTone =
          machine.rollout_state === "steady"
            ? "var(--accent-green)"
            : machine.rollout_state === "dirty" ||
                machine.rollout_state === "offline"
              ? "var(--accent-red)"
              : machine.rollout_state === "drifted" ||
                  machine.rollout_state === "degraded"
                ? "var(--accent-yellow)"
                : "inherit";
        return h(
          "div",
          { key: machine.name, className: "deployment-state-machine" },
          h(
            "div",
            { className: "deployment-state-machine-head" },
            h(
              "div",
              { className: "deployment-state-machine-title" },
              h("strong", null, machine.display_name || machine.name),
              h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    alignSelf: "flex-start",
                    background: "rgba(88,166,255,0.12)",
                    color: statusTone,
                  },
                },
                machine.rollout_label ||
                  machine.rollout_state ||
                  "unknown",
              ),
            ),
            h(
              "button",
              {
                className: "btn",
                style: { padding: "0 8px", fontSize: 11, height: 22 },
                onClick: function () {
                  previewUpdate(machine);
                },
                disabled: machine.rollout_state === "steady",
              },
              "Preview update",
            ),
          ),
          h(
            "div",
            { className: "deployment-state-fields" },
            h(
              "div",
              { className: "deployment-state-field" },
              h("span", null, "Desired"),
              h("code", null, renderVersion(machine.desired_version)),
            ),
            h(
              "div",
              { className: "deployment-state-field" },
              h("span", null, "Deployed"),
              h("code", null, renderVersion(machine.deployed_version)),
            ),
            h(
              "div",
              { className: "deployment-state-field" },
              h("span", null, "Drift"),
              h(
                "span",
                null,
                drift.severity || "unknown",
                drift.update_available ? " update available" : "",
              ),
            ),
            h(
              "div",
              { className: "deployment-state-field" },
              h("span", null, "Last health check"),
              h(
                "span",
                null,
                machine.last_health_check
                  ? timeAgo(machine.last_health_check)
                  : "not recorded",
              ),
            ),
            h(
              "div",
              { className: "deployment-state-field" },
              h("span", null, "Last rollback"),
              h("span", null, machine.last_rollback || "not recorded"),
            ),
          ),
          h(
            "div",
            {
              style: {
                fontSize: 12,
                color: "var(--text-muted)",
                lineHeight: 1.45,
              },
            },
            machine.rollout_detail ||
              drift.message ||
              "Deployment metadata unavailable.",
          ),
        );
      }),
    ),
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

  // Build machine list from runners even if fleet nodes aren't reachable.
  // Runner names are title-case (e.g. "Brick") but FLEET_NODES keys are
  // lowercase — match case-insensitively.
  var machineNames = Object.keys(runnersByMachine).sort(function (a, b) {
    return a === "ControlTower"
      ? -1
      : b === "ControlTower"
        ? 1
        : a.localeCompare(b);
  });
  var nodesByName = {};
  nodes.forEach(function (n) {
    nodesByName[canonicalMachineName(n.name).toLowerCase()] = n;
  });

  // Ensure every machine with runners has a node entry (even if dashboard unreachable)
  var allNodes = machineNames.map(function (name) {
    var node = nodesByName[name.toLowerCase()];
    if (node) {
      // Use runner-derived display name (title-case) and preserve backend fields
      return Object.assign({}, node, { name: name });
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

class LocalAppsErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
    this.handleRetry = this.handleRetry.bind(this);
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error: error };
  }
  componentDidCatch(error, info) {
    console.error("[LocalAppsTab] render error:", error, info);
  }
  handleRetry() {
    this.setState({ hasError: false, error: null });
  }
  render() {
    if (this.state.hasError) {
      return h("div", { style: { padding: 24, color: "var(--text-primary)" } },
        h("div", { style: { marginBottom: 12, color: "var(--accent-red)", fontWeight: 600 } }, "Local Tools failed to render"),
        h("code", { style: { display: "block", fontSize: 12, color: "var(--accent-red)", marginBottom: 12, whiteSpace: "pre-wrap" } },
          String(this.state.error)),
        h("button", { className: "btn", onClick: this.handleRetry, "aria-label": "Retry loading data" }, "Retry"),
      );
    }
    return this.props.children;
  }
}

function LocalAppsTab(p) {
  var data = p.data || {};
  var loading = p.loading;
  var onRefresh = p.onRefresh;
  var apps = Array.isArray(data.tools) ? data.tools : Array.isArray(data.apps) ? data.apps : [];

  function driftBadge(app) {
    var d = app.drift || {};
    var behind = d.behind;
    var ahead = d.ahead;
    if (!d.available) {
      var errMsg = d.error || "unavailable";
      return h(
        "span",
        {
          className: "section-badge",
          style: {
            background: "rgba(248,81,73,0.15)",
            color: "var(--accent-red)",
          },
          title: errMsg,
        },
        "\u26A0 error",
      );
    }
    if (behind === 0 && ahead === 0) {
      return h(
        "span",
        {
          className: "section-badge",
          style: {
            background: "rgba(63,185,80,0.15)",
            color: "var(--accent-green)",
          },
        },
        "\u2714 current",
      );
    }
    if (behind > 0 && ahead === 0) {
      return h(
        "span",
        {
          className: "section-badge",
          style: {
            background: "rgba(210,153,34,0.15)",
            color: "var(--accent-yellow)",
          },
        },
        "\u25BC " + behind + " behind",
      );
    }
    if (ahead > 0 && behind === 0) {
      return h(
        "span",
        {
          className: "section-badge",
          style: {
            background: "rgba(88,166,255,0.15)",
            color: "var(--accent-blue)",
          },
        },
        "\u25B2 " + ahead + " ahead",
      );
    }
    return h(
      "span",
      {
        className: "section-badge",
        style: {
          background: "rgba(248,81,73,0.15)",
          color: "var(--accent-red)",
        },
      },
      "\u2194 diverged",
    );
  }

  function healthBadge(app) {
    var h2 = app.health || {};
    if (!h2.available || h2.status === "not-configured") {
      return h(
        "span",
        {
          className: "section-badge",
          style: {
            background: "rgba(110,118,129,0.2)",
            color: "var(--text-muted)",
          },
        },
        "—",
      );
    }
    var ok = h2.ok !== false;
    return h(
      "span",
      {
        className: "section-badge",
        title: h2.status_code ? "HTTP " + h2.status_code : "",
        style: {
          background: ok
            ? "rgba(63,185,80,0.15)"
            : "rgba(248,81,73,0.15)",
          color: ok ? "var(--accent-green)" : "var(--accent-red)",
        },
      },
      ok
        ? "\u2714 " + (h2.status || "ok")
        : "\u2717 " + (h2.status || "fail"),
    );
  }

  function serviceBadge(status) {
    if (!status || status === "not-configured") return null;
    var ok = status === "active";
    return h(
      "span",
      {
        className: "section-badge",
        style: {
          background: ok
            ? "rgba(63,185,80,0.15)"
            : "rgba(248,81,73,0.15)",
          color: ok ? "var(--accent-green)" : "var(--accent-red)",
        },
      },
      status,
    );
  }

  function dirtyBadge(app) {
    if (app.dirty_available === false) {
      return h(
        "span",
        {
          className: "section-badge",
          title: app.dirty_error || "dirty probe failed",
          style: {
            background: "rgba(248,81,73,0.15)",
            color: "var(--accent-red)",
          },
        },
        "\u26A0 probe error",
      );
    }
    if (app.dirty) {
      return h(
        "span",
        {
          style: {
            color: "var(--accent-yellow)",
            fontWeight: 600,
          },
          title: (app.dirty_files || []).join("\n"),
        },
        app.dirty_files
          ? app.dirty_files.length +
              " file" +
              (app.dirty_files.length !== 1 ? "s" : "")
          : "yes",
      );
    }
    return h("span", { style: { color: "var(--text-muted)" } }, "clean");
  }

  var behindCount = apps.filter(localAppHasUpdateAvailable).length;
  var unhealthyCount = apps.filter(localAppUnhealthy).length;
  var dirtyCount = apps.filter(function (a) {
    return a.dirty;
  }).length;
  var dirtyErrorCount = apps.filter(function (a) {
    return a.dirty_available === false;
  }).length;

  return h(
    "div",
    { className: "section" },
    h(
      "div",
      {
        className: "section-header",
        style: { display: "flex", alignItems: "center", gap: 8 },
      },
      h("span", null, "Local Tools"),
      behindCount > 0
        ? h(
            "span",
            {
              className: "section-badge",
              style: {
                background: "rgba(210,153,34,0.15)",
                color: "var(--accent-yellow)",
              },
            },
            behindCount +
              " update" +
              (behindCount > 1 ? "s" : "") +
              " available",
          )
        : null,
      unhealthyCount > 0
        ? h(
            "span",
            {
              className: "section-badge",
              style: {
                background: "rgba(248,81,73,0.15)",
                color: "var(--accent-red)",
              },
            },
            unhealthyCount + " unhealthy",
          )
        : null,
      dirtyCount > 0
        ? h(
            "span",
            {
              className: "section-badge",
              style: {
                background: "rgba(210,153,34,0.15)",
                color: "var(--accent-yellow)",
              },
            },
            dirtyCount + " dirty",
          )
        : null,
      dirtyErrorCount > 0
        ? h(
            "span",
            {
              className: "section-badge",
              style: {
                background: "rgba(248,81,73,0.15)",
                color: "var(--accent-red)",
              },
            },
            dirtyErrorCount + " dirty probe error",
          )
        : null,
      h(
        "button",
        {
          className: "btn",
          style: { marginLeft: "auto" },
          onClick: onRefresh,
        },
        I.refresh(12),
      ),
    ),
    loading
      ? h(
          "div",
          { style: { padding: 24, color: "var(--text-muted)" } },
          "Loading\u2026",
        )
      : apps.length === 0
        ? h(
            "div",
            { style: { padding: 24, color: "var(--text-muted)" } },
            data.manifest_path
              ? "No tools defined in local_apps.json."
              : "No local_apps.json manifest found. Add one to runner-dashboard/ to start monitoring.",
          )
        : h(
            "table",
            { className: "data-table", style: { width: "100%" } },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h("th", null, "App"),
                h("th", null, "Drift Ref"),
                h("th", null, "Version"),
                h("th", null, "Drift"),
                h("th", null, "Dirty"),
                h("th", null, "Service"),
                h("th", null, "Health"),
              ),
            ),
            h(
              "tbody",
              null,
              apps.map(function (app) {
                return h(
                  React.Fragment,
                  { key: app.name },
                  h(
                    "tr",
                    null,
                    h(
                      "td",
                      null,
                      h("strong", null, app.name),
                      app.dirty
                        ? h(
                            "span",
                            {
                              className: "section-badge",
                              style: {
                                marginLeft: 4,
                                background: "rgba(210,153,34,0.15)",
                                color: "var(--accent-yellow)",
                              },
                              title:
                                "Uncommitted local changes:\n" +
                                (app.dirty_files || []).join("\n"),
                            },
                            "\u26A0 dirty",
                          )
                        : null,
                    ),
                    h(
                      "td",
                      {
                        style: {
                          fontFamily: "monospace",
                          fontSize: 12,
                          color: "var(--text-muted)",
                        },
                      },
                      (app.drift && app.drift.ref) || "\u2014",
                    ),
                    h(
                      "td",
                      {
                        style: {
                          fontFamily: "monospace",
                          fontSize: 12,
                          color: "var(--text-muted)",
                        },
                      },
                      renderVersion(
                        app.deployed_version ||
                          (app.deployment && app.deployment.version),
                      ),
                    ),
                    h("td", null, driftBadge(app)),
                    h("td", null, dirtyBadge(app)),
                    h("td", null, serviceBadge(app.service_status)),
                    h("td", null, healthBadge(app)),
                  ),
                  app.dirty &&
                    app.dirty_files &&
                    app.dirty_files.length > 0
                    ? h(
                        "tr",
                        null,
                        h(
                          "td",
                          {
                            colSpan: 7,
                            style: {
                              color: "var(--text-muted)",
                              fontSize: 11,
                              fontFamily: "monospace",
                              padding: "2px 12px 6px",
                            },
                          },
                          app.dirty_files.slice(0, 5).join(", ") +
                            (app.dirty_files.length > 5
                              ? " +" +
                                (app.dirty_files.length - 5) +
                                " more"
                              : ""),
                        ),
                      )
                    : null,
                );
              }),
            ),
          ),
  );
}


// ════════════════════════ SYSTEM RESOURCES PANEL ════════════════════════
function SystemResourcesPanel(p) {
  var sys = p.system || {};
  var cpu = sys.cpu || {};
  var mem = sys.memory || {};
  var disk = sys.disk || {};
  var diskPressure = disk.pressure || {};
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
                    color: v > 50 ? "#fff" : "var(--text-secondary)",
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
                return mem.used_gb + " / " + mem.total_gb + " GB (" + usedPct + "%)";
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
    disk.percent != null
      ? h(
          "div",
          { style: { marginBottom: 12 } },
          disk.windows_host
            ? h(
                "div",
                { style: { marginBottom: 8 } },
                h(
                  "div",
                  { className: "metric-row" },
                  h("span", { className: "metric-label" }, "Disk (Windows C:)"),
                  h(
                    "span",
                    { className: "metric-value" },
                    disk.windows_host.used_gb +
                      " / " +
                      disk.windows_host.total_gb +
                      " GB (" +
                      disk.windows_host.percent +
                      "%)",
                  ),
                ),
                h(
                  "div",
                  { className: "progress-bar" },
                  h("div", {
                    className: "progress-fill " + pColor(disk.windows_host.percent),
                    style: { width: disk.windows_host.percent + "%" },
                  }),
                ),
              )
            : null,
          h(
            "div",
            { className: "metric-row" },
            h("span", { className: "metric-label" }, disk.windows_host ? "Disk (WSL)" : "Disk"),
            h(
              "span",
              { className: "metric-value" },
              disk.used_gb +
                " / " +
                disk.total_gb +
                " GB (" +
                disk.percent +
                "%)",
            ),
          ),
          !disk.windows_host
            ? h(
                "div",
                { className: "progress-bar" },
                h("div", {
                  className: "progress-fill " + pColor(disk.percent),
                  style: { width: disk.percent + "%" },
                }),
              )
            : null,
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
  Brick: "var(--accent-green)",
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

// ════════════════════════ SCHEDULED JOBS TAB ════════════════════════
function ScheduledJobsTab(p) {
  var data = p.data;
  var loading = p.loading;
  var onRefresh = p.onRefresh;

  var repos = (data && data.repositories) || [];
  var totalScheduled = data ? data.scheduled_workflow_count || 0 : 0;
  var dryRunSteps =
    data && data.dry_run_plan
      ? (data.dry_run_plan.steps || []).length
      : 0;

  var filterState = React.useState("");
  var filterText = filterState[0],
    setFilterText = filterState[1];
  var filterRepoState = React.useState("all");
  var filterRepo = filterRepoState[0],
    setFilterRepo = filterRepoState[1];

  var allWorkflows = [];
  repos.forEach(function (repo) {
    (repo.workflows || []).forEach(function (wf) {
      if (!wf.scheduled) return;
      var entry = Object.assign({}, wf, { repo: repo.repository });
      allWorkflows.push(entry);
    });
  });

  var filtered = allWorkflows.filter(function (wf) {
    if (filterRepo !== "all" && wf.repo !== filterRepo) return false;
    if (filterText) {
      var q = filterText.toLowerCase();
      return (
        wf.workflow_name.toLowerCase().indexOf(q) !== -1 ||
        wf.repo.toLowerCase().indexOf(q) !== -1
      );
    }
    return true;
  });

  var julesCount = allWorkflows.filter(function (wf) {
    return wf.workflow_name.toLowerCase().indexOf("jules") !== -1;
  }).length;
  var disabledCount = allWorkflows.filter(function (wf) {
    return !wf.enabled;
  }).length;

  function conclusionColor(c) {
    if (c === "success") return "var(--accent-green)";
    if (c === "failure") return "var(--accent-red)";
    if (c === "cancelled") return "var(--fg-muted)";
    return "var(--accent-orange)";
  }

  function statusCell(wf) {
    if (!wf.enabled)
      return h(
        "span",
        {
          style: {
            fontSize: 11,
            padding: "2px 6px",
            borderRadius: 4,
            background: "rgba(139,148,158,0.15)",
            color: "var(--fg-muted)",
          },
        },
        "disabled",
      );
    var lr = wf.latest_run;
    if (!lr)
      return h(
        "span",
        {
          style: {
            fontSize: 11,
            padding: "2px 6px",
            borderRadius: 4,
            background: "rgba(139,148,158,0.1)",
            color: "var(--fg-muted)",
          },
        },
        "no runs",
      );
    if (lr.conclusion) {
      var cc = conclusionColor(lr.conclusion);
      return h(
        "span",
        {
          style: {
            fontSize: 11,
            padding: "2px 6px",
            borderRadius: 4,
            background: cc + "22",
            color: cc,
          },
        },
        lr.conclusion,
      );
    }
    return h(
      "span",
      {
        style: {
          fontSize: 11,
          padding: "2px 6px",
          borderRadius: 4,
          background: "rgba(88,166,255,0.15)",
          color: "var(--accent-blue)",
        },
      },
      lr.status || "running",
    );
  }

  var reposWithSchedules = repos.filter(function (r) {
    return r.scheduled_workflow_count > 0;
  });

  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Scheduled Workflows",
        value: totalScheduled,
        color: "var(--accent-blue)",
      }),
      h(Stat, {
        label: "Repos w/ Schedules",
        value: reposWithSchedules.length,
        color: "var(--accent-purple)",
      }),
      julesCount > 0
        ? h(Stat, {
            label: "Jules Schedules",
            value: julesCount,
            color: "var(--accent-green)",
          })
        : null,
      disabledCount > 0
        ? h(Stat, {
            label: "Disabled",
            value: disabledCount,
            color: "var(--accent-orange)",
          })
        : null,
      dryRunSteps > 0
        ? h(Stat, {
            label: "Dry-Run Actions",
            value: dryRunSteps,
            color: "var(--accent-red)",
          })
        : null,
    ),
    h(
      "div",
      { className: "section-header" },
      h(
        "span",
        { className: "section-title" },
        I.clock(14),
        " Scheduled Workflows",
        allWorkflows.length > 0
          ? h(
              "span",
              {
                className: "section-badge",
                style: {
                  background: "rgba(88,166,255,0.2)",
                  color: "var(--accent-blue)",
                  marginLeft: 4,
                },
              },
              filtered.length,
            )
          : null,
      ),
      h(
        "div",
        { style: { display: "flex", gap: 8, alignItems: "center" } },
        h("input", {
          type: "text",
          placeholder: "Filter by name or repo\u2026",
          value: filterText,
          onInput: function (e) {
            setFilterText(e.target.value);
          },
          style: {
            fontSize: 12,
            padding: "3px 8px",
            borderRadius: 4,
            border: "1px solid var(--border)",
            background: "var(--bg-card)",
            color: "var(--fg)",
            width: 180,
          },
        }),
        h(
          "select",
          {
            value: filterRepo,
            onChange: function (e) {
              setFilterRepo(e.target.value);
            },
            style: {
              fontSize: 12,
              padding: "3px 8px",
              borderRadius: 4,
              border: "1px solid var(--border)",
              background: "var(--bg-card)",
              color: "var(--fg)",
            },
          },
          h("option", { value: "all" }, "All repos"),
          reposWithSchedules.map(function (r) {
            return h(
              "option",
              { key: r.repository, value: r.repository },
              r.repository,
            );
          }),
        ),
        loading
          ? h(
              "span",
              { style: { fontSize: 11, color: "var(--fg-muted)" } },
              "Loading\u2026",
            )
          : null,
        data && data.generated_at
          ? h(
              "span",
              { style: { fontSize: 11, color: "var(--fg-muted)" } },
              "Updated " + timeAgo(data.generated_at),
            )
          : null,
        h(
          "button",
          { className: "btn", onClick: onRefresh },
          I.refresh(12),
        ),
      ),
    ),
    loading && allWorkflows.length === 0
      ? h(
          "div",
          {
            style: {
              textAlign: "center",
              padding: "40px 0",
              color: "var(--fg-muted)",
            },
          },
          "Loading scheduled workflows\u2026",
        )
      : filtered.length === 0
        ? h(
            "div",
            {
              style: {
                textAlign: "center",
                padding: "40px 0",
                color: "var(--fg-muted)",
              },
            },
            allWorkflows.length === 0
              ? "No scheduled workflows found."
              : "No workflows match the current filter.",
          )
        : h(
            "table",
            { className: "table" },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h("th", null, "Workflow"),
                h("th", null, "Repo"),
                h("th", null, "Cron"),
                h("th", null, "Status"),
                h("th", null, "Last Run"),
                h("th", null, "Conclusion"),
              ),
            ),
            h(
              "tbody",
              null,
              filtered.map(function (wf) {
                var lr = wf.latest_run;
                var isJules =
                  wf.workflow_name.toLowerCase().indexOf("jules") !== -1;
                return h(
                  "tr",
                  { key: wf.repo + "/" + wf.workflow_path },
                  h(
                    "td",
                    null,
                    isJules
                      ? h(
                          "span",
                          {
                            style: {
                              color: "var(--accent-purple)",
                              fontWeight: 600,
                              marginRight: 4,
                            },
                            title: "Jules workflow",
                          },
                          "\u25C6",
                        )
                      : null,
                    wf.workflow_name,
                  ),
                  h(
                    "td",
                    {
                      style: {
                        color: "var(--fg-muted)",
                        fontSize: 12,
                      },
                    },
                    wf.repo,
                  ),
                  h(
                    "td",
                    {
                      style: {
                        fontFamily: "monospace",
                        fontSize: 11,
                        color: "var(--fg-muted)",
                      },
                    },
                    wf.cron_expressions && wf.cron_expressions.length > 0
                      ? wf.cron_expressions.join(", ")
                      : h(
                          "span",
                          { style: { color: "var(--fg-muted)" } },
                          "\u2014",
                        ),
                  ),
                  h("td", null, statusCell(wf)),
                  h(
                    "td",
                    {
                      style: {
                        fontSize: 12,
                        color: "var(--fg-muted)",
                      },
                    },
                    lr ? timeAgo(lr.created_at) : "\u2014",
                  ),
                  h(
                    "td",
                    null,
                    lr && lr.html_url
                      ? h(
                          "a",
                          {
                            href: lr.html_url,
                            target: "_blank",
                            rel: "noopener noreferrer",
                            style: {
                              color: "var(--accent-blue)",
                              fontSize: 12,
                            },
                          },
                          lr.conclusion || lr.status || "in progress",
                        )
                      : h(
                          "span",
                          {
                            style: {
                              color: "var(--fg-muted)",
                              fontSize: 12,
                            },
                          },
                          "\u2014",
                        ),
                  ),
                );
              }),
            ),
          ),
    dryRunSteps > 0
      ? h(
          "div",
          null,
          h(
            "div",
            { className: "section-header", style: { marginTop: 16 } },
            h(
              "span",
              { className: "section-title" },
              "Dry-Run Plan",
              h(
                "span",
                {
                  className: "section-badge",
                  style: {
                    background: "rgba(255,165,0,0.15)",
                    color: "var(--accent-orange)",
                    marginLeft: 4,
                  },
                },
                dryRunSteps,
              ),
            ),
            h(
              "span",
              { style: { fontSize: 11, color: "var(--fg-muted)" } },
              "Read-only \u2014 no write actions will be performed",
            ),
          ),
          h(
            "table",
            { className: "table" },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h("th", null, "Action"),
                h("th", null, "Workflow"),
                h("th", null, "Repo"),
                h("th", null, "Reason"),
              ),
            ),
            h(
              "tbody",
              null,
              (data.dry_run_plan.steps || []).map(function (step, idx) {
                return h(
                  "tr",
                  { key: idx },
                  h(
                    "td",
                    null,
                    h("code", { style: { fontSize: 11 } }, step.action),
                  ),
                  h(
                    "td",
                    { style: { fontSize: 12 } },
                    step.workflow_name,
                  ),
                  h(
                    "td",
                    {
                      style: {
                        fontSize: 12,
                        color: "var(--fg-muted)",
                      },
                    },
                    step.repository,
                  ),
                  h(
                    "td",
                    {
                      style: {
                        fontSize: 11,
                        color: "var(--fg-muted)",
                        maxWidth: 320,
                      },
                    },
                    step.reason,
                  ),
                );
              }),
            ),
          ),
        )
      : null,
  );
}

function RunnerScheduleTab(p) {
  var data = p.data || {};
  var schedule = data.schedule || {};
  var state = data.state || {};
  var draftState = React.useState(schedule);
  var draft = draftState[0] || schedule;
  var setDraft = draftState[1];
  var schedules = draft.schedules || [];
  var loading = p.loading;
  React.useEffect(
    function () {
      setDraft(schedule);
    },
    [data.config_path, state.timestamp],
  );
  function updateSchedule(index, key, value) {
    var next = Object.assign({}, draft, {
      schedules: schedules.map(function (entry, i) {
        if (i !== index) return entry;
        var copy = Object.assign({}, entry);
        copy[key] = key === "runners" ? Number(value) : value;
        return copy;
      }),
    });
    setDraft(next);
  }
  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Desired",
        value: state.desired != null ? state.desired : "-",
        color: "var(--accent-blue)",
        sub: state.reason || "schedule",
      }),
      h(Stat, {
        label: "Online",
        value: state.online != null ? state.online : "-",
        color: "var(--accent-green)",
        sub:
          "installed " +
          (state.installed != null ? state.installed : "-"),
      }),
      h(Stat, {
        label: "Busy",
        value: state.busy != null ? state.busy : "-",
        color: "var(--accent-orange)",
        sub: "idle " + (state.idle != null ? state.idle : "-"),
      }),
      h(Stat, {
        label: "Offline",
        value: state.offline != null ? state.offline : "-",
        color: "var(--accent-red)",
        sub: "max " + (data.max_runners || "-"),
      }),
    ),
    h(
      "div",
      { className: "section-header" },
      h(
        "span",
        { className: "section-title" },
        I.clock(14),
        " Runner Capacity",
      ),
      h(
        "div",
        { style: { display: "flex", gap: 8, alignItems: "center" } },
        loading
          ? h(
              "span",
              { style: { fontSize: 11, color: "var(--fg-muted)" } },
              "Saving...",
            )
          : null,
        h(
          "button",
          { className: "btn", onClick: p.onRefresh },
          I.refresh(12),
        ),
        h(
          "button",
          {
            className: "btn",
            onClick: function () {
              p.onSave(draft, false);
            },
            disabled: loading || !draft.schedules,
          },
          "Save",
        ),
        h(
          "button",
          {
            className: "btn",
            onClick: function () {
              p.onSave(draft, true);
            },
            disabled: loading || !draft.schedules,
          },
          I.play(12),
          "Apply Now",
        ),
      ),
    ),
    h(
      "div",
      { className: "card", style: { marginBottom: 12 } },
      h(
        "div",
        {
          style: {
            fontSize: 13,
            color: "var(--fg-muted)",
            marginBottom: 12,
          },
        },
        (data.machine || "Local machine") +
          (data.aliases && data.aliases.length
            ? " aliases: " + data.aliases.join(", ")
            : "") +
          " | scheduler " +
          (state.available ? "installed" : "missing"),
      ),
      state.error
        ? h(
            "div",
            {
              style: {
                color: "var(--accent-orange)",
                fontSize: 12,
                marginBottom: 12,
              },
            },
            state.error,
          )
        : null,
      h(
        "table",
        { className: "table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", null, "Name"),
            h("th", null, "Days"),
            h("th", null, "Start"),
            h("th", null, "End"),
            h("th", null, "Runners"),
          ),
        ),
        h(
          "tbody",
          null,
          schedules.map(function (entry, index) {
            return h(
              "tr",
              { key: entry.name + index },
              h("td", null, entry.name),
              h(
                "td",
                { style: { fontSize: 12 } },
                (entry.days || []).join(", "),
              ),
              h(
                "td",
                null,
                h("input", {
                  value: entry.start,
                  onInput: function (e) {
                    updateSchedule(index, "start", e.target.value);
                  },
                  style: { width: 76 },
                }),
              ),
              h(
                "td",
                null,
                h("input", {
                  value: entry.end,
                  onInput: function (e) {
                    updateSchedule(index, "end", e.target.value);
                  },
                  style: { width: 76 },
                }),
              ),
              h(
                "td",
                null,
                h("input", {
                  type: "number",
                  min: 0,
                  max: data.max_runners || 99,
                  value: entry.runners,
                  onInput: function (e) {
                    updateSchedule(index, "runners", e.target.value);
                  },
                  style: { width: 64 },
                }),
              ),
            );
          }),
        ),
      ),
      h(
        "div",
        {
          style: {
            fontSize: 12,
            color: "var(--fg-muted)",
            marginTop: 12,
          },
        },
        "Config: " +
          (data.config_path || "-") +
          " | timers: scheduler " +
          ((data.timers && data.timers["runner-scheduler.timer"]) ||
            "-") +
          ", cleanup " +
          ((data.timers && data.timers["runner-cleanup.timer"]) || "-"),
      ),
    ),
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
    fetch("/api/prs?limit=2000")
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
    fetch("/api/prs/dispatch", {
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
          },
          h(
            "div",
            {
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
              { style: { fontSize: 15, fontWeight: 600, marginBottom: 12 } },
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
    fetch('/api/issues?limit=2000&source=' + encodeURIComponent(activeSource))
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
    fetch('/api/linear/workspaces')
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

  function getTypeStyle(type) {
    var map = {
      epic: { background: 'rgba(110,118,129,0.2)', color: '#8b949e' },
      task: { background: 'rgba(88,166,255,0.2)', color: '#58a6ff' },
      bug: { background: 'rgba(248,81,73,0.2)', color: '#f85149' },
      security: { background: 'rgba(248,81,73,0.2)', color: '#f85149' },
      research: { background: 'rgba(188,140,255,0.2)', color: '#bc8cff' },
      docs: { background: 'rgba(56,189,248,0.2)', color: '#38bdf8' },
      chore: { background: 'rgba(110,118,129,0.2)', color: '#8b949e' },
    };
    return map[type] || { background: 'rgba(110,118,129,0.15)', color: '#8b949e' };
  }

  function getComplexityStyle(complexity) {
    var map = {
      trivial: { background: 'rgba(63,185,80,0.2)', color: '#3fb950' },
      routine: { background: 'rgba(88,166,255,0.2)', color: '#58a6ff' },
      complex: { background: 'rgba(210,153,34,0.2)', color: '#d2993a' },
      deep: { background: 'rgba(248,81,73,0.2)', color: '#f85149' },
      research: { background: 'rgba(188,140,255,0.2)', color: '#bc8cff' },
    };
    return map[complexity] || { background: 'rgba(110,118,129,0.15)', color: '#8b949e' };
  }

  function getJudgementStyle(judgement) {
    if (judgement === 'design' || judgement === 'contested') {
      return { background: 'rgba(220,38,38,0.2)', color: '#ef4444' };
    }
    var map = {
      objective: { background: 'rgba(63,185,80,0.15)', color: '#3fb950' },
      preference: { background: 'rgba(210,153,34,0.15)', color: '#d2993a' },
    };
    return map[judgement] || { background: 'rgba(110,118,129,0.15)', color: '#8b949e' };
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
    fetch('/api/issues/dispatch', {
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
                        style: pillStyle(src === 'linear' ? { background: 'rgba(99,102,241,0.18)', color: '#a5b4fc' } : { background: 'rgba(88,166,255,0.18)', color: '#58a6ff' }),
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
                  taxonomy.quick_win ? h('span', { style: { color: '#f0c040', marginRight: 4 } }, '★') : null,
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
                    ? h('span', { style: { color: '#3fb950', fontSize: 14 } }, '✓')
                    : h('span', { title: !dispatchable ? 'Linear-only items cannot be dispatched until linked to a GitHub issue.' : blockedBy.join(', '), style: { color: '#f85149', fontSize: 14, cursor: 'help' } }, '✗')
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
    },
      h('div', {
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
        h('h3', { style: { margin: '0 0 12px 0', fontSize: 15 } }, 'Confirm Dispatch'),

        hasDangerous ? h('div', {
          style: {
            marginBottom: 12,
            padding: '8px 12px',
            borderRadius: 6,
            background: 'rgba(220,38,38,0.15)',
            color: '#ef4444',
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
              issue.pickable === false ? h('span', { style: { color: '#f85149', marginLeft: 6 } }, '(non-pickable)') : null
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
          h('span', { style: { color: '#f85149', fontWeight: 600 } }, 'Force dispatch (include non-pickable issues)')
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
                    ? "#58a6ff"
                    : triggerType === "scheduled"
                      ? "#a371f7"
                      : triggerType === "workflow_run"
                        ? "#8b949e"
                        : "#e3b341";
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
                                color: "#58a6ff",
                                cursor: "pointer",
                              },
                              onClick: function () {
                                fetch(
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

function WorkflowsTab(p) {
  var workflows = p.workflows || [];
  var loading = p.loading;
  var error = p.error;
  var onDispatch = p.onDispatch;
  var onRefresh = p.onRefresh;

  var savedFilters = {};
  try {
    savedFilters = JSON.parse(
      sessionStorage.getItem("workflowsMobileFilters") || "{}",
    );
  } catch (e) {
    savedFilters = {};
  }
  var sf = React.useState(savedFilters.search || "");
  var searchFilter = sf[0],
    setSearchFilter = sf[1];
  var rf = React.useState(savedFilters.repo || "all");
  var repoFilter = rf[0],
    setRepoFilter = rf[1];
  var tf = React.useState(savedFilters.trigger || "all");
  var triggerFilter = tf[0],
    setTriggerFilter = tf[1];
  var ex = React.useState(null);
  var expandedId = ex[0],
    setExpandedId = ex[1];
  var ds = React.useState(null);
  var dispatchingWf = ds[0],
    setDispatchingWf = ds[1];
  var dm = React.useState({});
  var dispatchModal = dm[0],
    setDispatchModal = dm[1];
  var dc = React.useState(false);
  var dispatchConfirm = dc[0],
    setDispatchConfirm = dc[1];

  React.useEffect(
    function () {
      sessionStorage.setItem(
        "workflowsMobileFilters",
        JSON.stringify({
          search: searchFilter,
          repo: repoFilter,
          trigger: triggerFilter,
        }),
      );
    },
    [searchFilter, repoFilter, triggerFilter],
  );

  var repos = Array.from(
    new Set(
      workflows.map(function (w) {
        return w.repository;
      }),
    ),
  ).sort();

  var filtered = workflows.filter(function (w) {
    if (repoFilter !== "all" && w.repository !== repoFilter) return false;
    if (
      triggerFilter !== "all" &&
      !(w.triggers || []).includes(triggerFilter)
    )
      return false;
    if (searchFilter) {
      var q = searchFilter.toLowerCase();
      if (
        !w.name.toLowerCase().includes(q) &&
        !w.repository.toLowerCase().includes(q)
      )
        return false;
    }
    return true;
  });

  var byRepo = {};
  filtered.forEach(function (w) {
    if (!byRepo[w.repository]) byRepo[w.repository] = [];
    byRepo[w.repository].push(w);
  });

  function triggerBadge(trigger) {
    var colors = {
      manual: {
        bg: "rgba(88,166,255,0.15)",
        color: "var(--accent-blue, #58a6ff)",
      },
      schedule: {
        bg: "rgba(136,108,228,0.15)",
        color: "var(--accent-purple)",
      },
      push_pr: {
        bg: "rgba(63,185,80,0.15)",
        color: "var(--accent-green)",
      },
      workflow_run: {
        bg: "rgba(210,153,34,0.15)",
        color: "var(--accent-yellow)",
      },
    };
    var c = colors[trigger] || {
      bg: "rgba(139,148,158,0.15)",
      color: "var(--text-muted)",
    };
    return h(
      "span",
      {
        key: trigger,
        className: "section-badge",
        style: { background: c.bg, color: c.color, marginRight: 4 },
      },
      trigger,
    );
  }

  function conclusionColor(conclusion) {
    if (conclusion === "success") return "var(--accent-green)";
    if (conclusion === "failure") return "var(--accent-red)";
    if (conclusion === "cancelled") return "var(--text-muted)";
    return "var(--accent-yellow)";
  }

  function openDispatch(wf) {
    setDispatchModal({ wf: wf, ref: "main", inputs: {} });
    setDispatchConfirm(false);
  }

  function doDispatch() {
    var wf = dispatchModal.wf;
    if (!wf) return;
    setDispatchingWf(wf.id);
    onDispatch({
      repository: wf.repository,
      workflow_id: wf.id,
      ref: dispatchModal.ref || "main",
      inputs: dispatchModal.inputs || {},
    }).finally(function () {
      setDispatchingWf(null);
      setDispatchModal({});
      setDispatchConfirm(false);
    });
  }

  return h(
    "div",
    null,
    h(
      "div",
      {
        className: "mobile-workflow-filters",
        style: {
          display: "flex",
          gap: 8,
          marginBottom: 16,
          flexWrap: "wrap",
          alignItems: "center",
        },
      },
      h("input", {
        type: "text",
        placeholder: "Search workflows or repos…",
        value: searchFilter,
        onChange: function (e) {
          setSearchFilter(e.target.value);
        },
        style: {
          flex: "1 1 200px",
          minWidth: 180,
          background: "var(--bg-secondary)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "7px 10px",
          fontSize: 13,
        },
      }),
      h(
        "select",
        {
          value: repoFilter,
          onChange: function (e) {
            setRepoFilter(e.target.value);
          },
          style: {
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "7px 10px",
            fontSize: 13,
          },
        },
        h("option", { value: "all" }, "All repos"),
        repos.map(function (r) {
          return h("option", { key: r, value: r }, r);
        }),
      ),
      h(
        "select",
        {
          value: triggerFilter,
          onChange: function (e) {
            setTriggerFilter(e.target.value);
          },
          style: {
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "7px 10px",
            fontSize: 13,
          },
        },
        h("option", { value: "all" }, "All triggers"),
        h("option", { value: "manual" }, "Manual dispatch"),
        h("option", { value: "schedule" }, "Scheduled"),
        h("option", { value: "push_pr" }, "Push/PR"),
        h("option", { value: "workflow_run" }, "Workflow run"),
      ),
      h(
        "button",
        { className: "btn", onClick: onRefresh, disabled: loading },
        I.refresh(12),
        loading ? "Loading…" : "Refresh",
      ),
      h(
        "span",
        { style: { fontSize: 12, color: "var(--text-muted)" } },
        filtered.length + " workflows",
      ),
    ),
    loading && !workflows.length
      ? h(
          "div",
          {
            style: {
              color: "var(--text-muted)",
              textAlign: "center",
              padding: 32,
            },
          },
          "Loading workflows…",
        )
      : null,
    error
      ? h(
          "div",
          {
            style: {
              padding: "10px 12px",
              borderRadius: 8,
              background: "rgba(248,81,73,0.12)",
              color: "var(--accent-red)",
              fontSize: 12,
              marginBottom: 12,
            },
          },
          error,
        )
      : null,
    Object.keys(byRepo)
      .sort()
      .map(function (repoName) {
        var repoWfs = byRepo[repoName];
        return h(
          "div",
          {
            key: repoName,
            className: "section",
            style: { marginBottom: 12 },
          },
          h(
            "div",
            { className: "section-header" },
            h(
              "span",
              { className: "section-title" },
              I.server(14),
              repoName,
            ),
            h(
              "span",
              { className: "section-badge" },
              repoWfs.length + " workflows",
            ),
          ),
          h(
            "div",
            { className: "section-body", style: { padding: 0 } },
            repoWfs.map(function (wf) {
              var expanded = expandedId === wf.id;
              var lr = wf.latest_run;
              return h(
                "div",
                {
                  key: wf.id,
                  style: {
                    padding: "10px 14px",
                    borderBottom: "1px solid var(--border)",
                    cursor: "pointer",
                  },
                  onClick: function () {
                    setExpandedId(expanded ? null : wf.id);
                  },
                },
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      flexWrap: "wrap",
                    },
                  },
                  h(
                    "span",
                    {
                      style: {
                        fontWeight: 600,
                        fontSize: 13,
                        flex: 1,
                        minWidth: 0,
                      },
                    },
                    wf.name,
                  ),
                  (wf.triggers || []).map(triggerBadge),
                  lr
                    ? h(
                        "span",
                        {
                          style: {
                            fontSize: 12,
                            color: conclusionColor(lr.conclusion),
                            fontWeight: 500,
                          },
                        },
                        lr.conclusion || lr.status || "?",
                      )
                    : null,
                  wf.triggers && wf.triggers.includes("manual")
                    ? h(
                        "button",
                        {
                          className: "btn",
                          style: { fontSize: 11, padding: "3px 10px" },
                          disabled: dispatchingWf === wf.id,
                          onClick: function (e) {
                            e.stopPropagation();
                            openDispatch(wf);
                          },
                        },
                        dispatchingWf === wf.id ? "Dispatching…" : "Run",
                      )
                    : null,
                  wf.html_url
                    ? h(
                        "a",
                        {
                          href: wf.html_url,
                          target: "_blank",
                          rel: "noopener noreferrer",
                          style: {
                            fontSize: 11,
                            color: "var(--text-muted)",
                          },
                          onClick: function (e) {
                            e.stopPropagation();
                          },
                        },
                        "↗",
                      )
                    : null,
                ),
                expanded
                  ? h(
                      "div",
                      { style: { marginTop: 8, fontSize: 12 } },
                      h(
                        "div",
                        {
                          style: {
                            color: "var(--text-secondary)",
                            marginBottom: 6,
                          },
                        },
                        "Recent runs:",
                      ),
                      (wf.recent_runs || []).length === 0
                        ? h(
                            "span",
                            { style: { color: "var(--text-muted)" } },
                            "No recent runs",
                          )
                        : (wf.recent_runs || []).map(function (r) {
                            return h(
                              "div",
                              {
                                key: r.id,
                                style: {
                                  display: "flex",
                                  gap: 8,
                                  alignItems: "center",
                                  marginBottom: 4,
                                },
                              },
                              h(
                                "span",
                                {
                                  style: {
                                    color: conclusionColor(r.conclusion),
                                    minWidth: 60,
                                  },
                                },
                                r.conclusion || r.status || "?",
                              ),
                              h(
                                "span",
                                { style: { color: "var(--text-muted)" } },
                                r.created_at
                                  ? r.created_at.slice(0, 10)
                                  : "",
                              ),
                              r.html_url
                                ? h(
                                    "a",
                                    {
                                      href: r.html_url,
                                      target: "_blank",
                                      rel: "noopener noreferrer",
                                      style: {
                                        color: "var(--text-secondary)",
                                      },
                                    },
                                    "#" + r.id,
                                  )
                                : null,
                            );
                          }),
                    )
                  : null,
              );
            }),
          ),
        );
      }),
    dispatchModal && dispatchModal.wf
      ? h(
          "div",
          {
            style: {
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.5)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1000,
            },
          },
          h(
            "div",
            {
              style: {
                background: "var(--bg-primary)",
                border: "1px solid var(--border)",
                borderRadius: 12,
                padding: 24,
                minWidth: 360,
                maxWidth: 480,
              },
            },
            h(
              "div",
              {
                style: {
                  fontSize: 15,
                  fontWeight: 600,
                  marginBottom: 12,
                },
              },
              "Dispatch: " + dispatchModal.wf.name,
            ),
            h(
              "div",
              {
                style: {
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  marginBottom: 8,
                },
              },
              "Repository: " + dispatchModal.wf.repository,
            ),
            h(
              "label",
              {
                style: {
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  display: "block",
                  marginBottom: 6,
                },
              },
              "Ref (branch/tag):",
              h("input", {
                type: "text",
                value: dispatchModal.ref || "main",
                onChange: function (e) {
                  setDispatchModal(function (prev) {
                    return Object.assign({}, prev, {
                      ref: e.target.value,
                    });
                  });
                },
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
              }),
            ),
            !dispatchConfirm
              ? h(
                  "div",
                  { style: { display: "flex", gap: 8, marginTop: 16 } },
                  h(
                    "button",
                    {
                      className: "btn",
                      onClick: function () {
                        setDispatchConfirm(true);
                      },
                    },
                    "Confirm dispatch",
                  ),
                  h(
                    "button",
                    {
                      className: "btn",
                      style: { opacity: 0.7 },
                      onClick: function () {
                        setDispatchModal({});
                      },
                    },
                    "Cancel",
                  ),
                )
              : h(
                  "div",
                  null,
                  h(
                    "div",
                    {
                      style: {
                        fontSize: 12,
                        color: "var(--accent-yellow)",
                        marginBottom: 10,
                        padding: "8px 10px",
                        background: "rgba(210,153,34,0.1)",
                        borderRadius: 6,
                      },
                    },
                    "This will trigger " +
                      dispatchModal.wf.name +
                      " on " +
                      dispatchModal.wf.repository +
                      " at ref " +
                      (dispatchModal.ref || "main") +
                      ".",
                  ),
                  h(
                    "div",
                    { style: { display: "flex", gap: 8 } },
                    h(
                      "button",
                      {
                        className: "btn",
                        onClick: doDispatch,
                        disabled: !!dispatchingWf,
                      },
                      dispatchingWf ? "Dispatching…" : "Dispatch now",
                    ),
                    h(
                      "button",
                      {
                        className: "btn",
                        style: { opacity: 0.7 },
                        onClick: function () {
                          setDispatchModal({});
                          setDispatchConfirm(false);
                        },
                      },
                      "Cancel",
                    ),
                  ),
                ),
          ),
        )
      : null,
  );
}

function CredentialsTab(p) {
  var probes = p.probes || [];
  var summary = p.summary || {};
  var loading = p.loading;
  var error = p.error;
  var onRefresh = p.onRefresh;
  var onSetKey = p.onSetKey;
  var mobile = p.mobile;
  var lockState = React.useState(false);
  var mobileUnlocked = lockState[0],
    setMobileUnlocked = lockState[1];
  var statusState = React.useState(null);
  var mobileUnlockStatus = statusState[0],
    setMobileUnlockStatus = statusState[1];
  var confirmingState = React.useState(null);
  var mobileConfirmProbe = confirmingState[0],
    setMobileConfirmProbe = confirmingState[1];

  function base64UrlToBuffer(value) {
    var padded = value.replace(/-/g, "+").replace(/_/g, "/");
    padded += "=".repeat((4 - (padded.length % 4)) % 4);
    var raw = window.atob(padded);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
    return out.buffer;
  }

  function credentialToPayload(credential) {
    if (!credential) return {};
    return {
      id: credential.id,
      type: credential.type,
      rawId: credential.rawId ? Array.from(new Uint8Array(credential.rawId)) : [],
      response: credential.response
        ? {
            authenticatorData: credential.response.authenticatorData
              ? Array.from(new Uint8Array(credential.response.authenticatorData))
              : [],
            clientDataJSON: credential.response.clientDataJSON
              ? Array.from(new Uint8Array(credential.response.clientDataJSON))
              : [],
            signature: credential.response.signature
              ? Array.from(new Uint8Array(credential.response.signature))
              : [],
            userHandle: credential.response.userHandle
              ? Array.from(new Uint8Array(credential.response.userHandle))
              : null,
          }
        : {},
    };
  }

  function lockMobileCredentials(message) {
    setMobileUnlocked(false);
    setMobileConfirmProbe(null);
    if (message) setMobileUnlockStatus(message);
  }

  function requestMobileCredentialUnlock() {
    setMobileUnlockStatus("Requesting biometric assertion...");
    if (!window.PublicKeyCredential || !navigator.credentials || !navigator.credentials.get) {
      lockMobileCredentials("This browser does not expose WebAuthn credentials.");
      return;
    }
    fetch("/api/auth/webauthn/assert/begin", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: "{}",
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error((data && data.detail) || "WebAuthn assertion failed to start");
          return data;
        });
      })
      .then(function (data) {
        return navigator.credentials.get({
          publicKey: {
            challenge: base64UrlToBuffer(data.challenge),
            allowCredentials: (data.allow_credentials || []).map(function (cred) {
              return { id: base64UrlToBuffer(cred.id), type: cred.type || "public-key" };
            }),
            timeout: data.timeout_ms || 60000,
            userVerification: "required",
          },
        });
      })
      .then(function (credential) {
        return fetch("/api/auth/webauthn/assert/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
          body: JSON.stringify({ credential: credentialToPayload(credential) }),
        }).then(function (r) {
          return r.json().then(function (data) {
            if (!r.ok) throw new Error((data && data.detail) || "WebAuthn assertion was not accepted");
            return data;
          });
        });
      })
      .then(function () {
        setMobileUnlocked(true);
        setMobileUnlockStatus("Unlocked for 60 seconds.");
        if (onRefresh) onRefresh();
      })
      .catch(function (err) {
        lockMobileCredentials((err && err.message) || "Biometric assertion failed.");
      });
  }

  React.useEffect(function () {
    if (!mobile || !mobileUnlocked) return;
    var timer = window.setTimeout(function () {
      lockMobileCredentials("Credentials re-locked after 60 seconds.");
    }, 60000);
    function onVisibilityChange() {
      if (document.hidden) lockMobileCredentials("Credentials re-locked when the tab lost focus.");
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return function () {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [mobile, mobileUnlocked]);

  function statusColor(status) {
    if (status === "ready") return "var(--accent-green)";
    if (status === "not_installed") return "var(--text-muted)";
    if (status === "missing_key" || status === "not_authed" || status === "missing_env")
      return "var(--accent-yellow)";
    return "var(--accent-red)";
  }
  function statusBg(status) {
    if (status === "ready") return "rgba(63,185,80,0.12)";
    if (status === "not_installed") return "rgba(139,148,158,0.12)";
    if (status === "missing_key" || status === "not_authed" || status === "missing_env")
      return "rgba(210,153,34,0.12)";
    return "rgba(248,81,73,0.12)";
  }
  function statusLabel(status) {
    var labels = {
      ready: "Ready",
      not_installed: "Not installed",
      missing_key: "Missing key",
      missing_env: "Missing key",
      not_authed: "Not authenticated",
      auth_failed: "Auth failed",
      probe_failed: "Probe failed",
    };
    return labels[status] || status;
  }

  if (mobile && !mobileUnlocked) {
    return h(
      "div",
      { className: "mobile-credentials-lock" },
      h("div", { style: { fontSize: 16, fontWeight: 700, marginBottom: 6 } }, "Credentials locked"),
      h(
        "div",
        { style: { color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.45, marginBottom: 14 } },
        "Mobile access to credential metadata requires a fresh biometric assertion. Secret values are never shown.",
      ),
      h(
        "button",
        { className: "btn", onClick: requestMobileCredentialUnlock },
        "Show credentials",
      ),
      mobileUnlockStatus
        ? h("div", { style: { marginTop: 12, color: "var(--text-muted)", fontSize: 12 } }, mobileUnlockStatus)
        : null,
    );
  }

  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Ready",
        value: summary.ready || 0,
        sub: "providers available",
      }),
      h(Stat, {
        label: "Not ready",
        value: summary.not_ready || 0,
        sub: "need setup",
      }),
      h(Stat, {
        label: "Total",
        value: summary.total || probes.length,
        sub: "providers probed",
      }),
    ),
    h(
      "div",
      { style: { display: "flex", gap: 8, marginBottom: 16, alignItems: "center" } },
      h("button", { className: "btn", onClick: onRefresh, disabled: loading, "aria-label": "Re-probe runner status" }, I.refresh(12), loading ? "Probing..." : "Re-probe"),
      h("span", { style: { fontSize: 12, color: "var(--text-muted)" } }, "Probes run locally. No secrets are shown."),
    ),
    error
      ? h("div", { style: { padding: "10px 12px", borderRadius: 8, background: "rgba(248,81,73,0.12)", color: "var(--accent-red)", fontSize: 12, marginBottom: 12 } }, error)
      : null,
    h(
      "div",
      { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 12 } },
      probes.map(function (probe) {
        return h(
          "div",
          { key: probe.id || probe.name, style: { background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 10, padding: 16 } },
          h("div", { style: { display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" } },
            h("strong", null, probe.label || probe.name || probe.id || "Provider"),
            h("span", { className: "section-badge", style: { background: statusBg(probe.status), color: statusColor(probe.status) } }, statusLabel(probe.status)),
          ),
          probe.detail ? h("div", { style: { marginTop: 8, fontSize: 12, color: "var(--text-muted)" } }, probe.detail) : null,
          probe.setup_hint ? h("div", { style: { marginTop: 8, fontSize: 12, color: probe.usable ? "var(--text-secondary)" : "var(--accent-yellow)" } }, probe.setup_hint) : null,
          h(
            "div",
            { style: { marginTop: 12, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" } },
            probe.key_provider && onSetKey
              ? h(
                  "button",
                  {
                    className: "btn",
                    style: { fontSize: 12, padding: "6px 10px" },
                    onClick: function () {
                      if (mobile) {
                        setMobileConfirmProbe(probe);
                        return;
                      }
                      onSetKey(probe);
                    },
                  },
                  probe.usable ? "Replace API key" : "Set API key",
                )
              : null,
            probe.docs_url
              ? h(
                  "a",
                  {
                    href: probe.docs_url,
                    target: "_blank",
                    rel: "noopener noreferrer",
                    style: { fontSize: 12, color: "var(--text-secondary)" },
                  },
                  "Docs ↗",
                )
              : null,
          ),
        );
      }),
    ),
    mobile && mobileConfirmProbe
      ? h(
          "div",
          {
            className: "mobile-credentials-sheet",
            role: "dialog",
            "aria-modal": "true",
            "aria-label": "Confirm mobile credential change",
            onClick: function () {
              setMobileConfirmProbe(null);
            },
          },
          h(
            "div",
            {
              className: "mobile-credentials-sheet-panel",
              onClick: function (e) {
                e.stopPropagation();
              },
            },
            h("div", { style: { fontSize: 16, fontWeight: 700, marginBottom: 8 } }, "Confirm sensitive operation"),
            h(
              "div",
              { style: { color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.45, marginBottom: 14 } },
              "This will open the server-side key update flow for " +
                (mobileConfirmProbe.label || mobileConfirmProbe.name || mobileConfirmProbe.key_provider || "this provider") +
                ". Continue only if you intend to change credential state.",
            ),
            h(
              "div",
              { style: { display: "flex", gap: 8, justifyContent: "flex-end" } },
              h(
                "button",
                {
                  className: "btn",
                  onClick: function () {
                    setMobileConfirmProbe(null);
                  },
                },
                "Cancel",
              ),
              h(
                "button",
                {
                  className: "btn",
                  style: { background: "var(--accent-red)", color: "white" },
                  onClick: function () {
                    var probe = mobileConfirmProbe;
                    setMobileConfirmProbe(null);
                    onSetKey(probe);
                  },
                },
                "Confirm",
              ),
            ),
          ),
        )
      : null,
  );
}

function MaxwellTab(p) {
  var status = p.status || {};
  var loading = p.loading;
  var error = p.error;
  var onRefresh = p.onRefresh;
  var onControl = p.onControl;
  var cs = React.useState(null);
  var controlStatus = cs[0],
    setControlStatus = cs[1];
  var cl = React.useState(false);
  var controlling = cl[0],
    setControlling = cl[1];
  var pending = React.useState("");
  var pendingAction = pending[0],
    setPendingAction = pending[1];
  var ts = React.useState([]);
  var tasks = ts[0],
    setTasks = ts[1];
  var tl = React.useState(false);
  var tasksLoading = tl[0],
    setTasksLoading = tl[1];
  var dv = React.useState("");
  var daemonVersion = dv[0],
    setDaemonVersion = dv[1];
  var chatStoreKey = "maxwellMobileChatHistory";
  var cm = React.useState(function () {
    try {
      return JSON.parse(sessionStorage.getItem(chatStoreKey) || "[]");
    } catch (e) {
      return [];
    }
  });
  var chatMessages = cm[0],
    setChatMessages = cm[1];
  var ci = React.useState("");
  var chatInput = ci[0],
    setChatInput = ci[1];
  var csending = React.useState(false);
  var chatSending = csending[0],
    setChatSending = csending[1];
  var cscroll = React.useState(false);
  var showScrollButton = cscroll[0],
    setShowScrollButton = cscroll[1];
  var chatListRef = React.useRef(null);
  var isRunning = status.status === "running";

  function fetchTasks() {
    setTasksLoading(true);
    fetch("/api/maxwell/tasks?limit=10")
      .then(function (r) { return r.json(); })
      .then(function (data) { setTasks(data.tasks || []); })
      .catch(function () { setTasks([]); })
      .finally(function () { setTasksLoading(false); });
  }

  function fetchVersion() {
    fetch("/api/maxwell/version")
      .then(function (r) { return r.json(); })
      .then(function (data) { setDaemonVersion(data.contract || data.daemon || ""); })
      .catch(function () { setDaemonVersion(""); });
  }

  React.useEffect(function () {
    fetchTasks();
    fetchVersion();
  }, []);

  React.useEffect(function () {
    try {
      sessionStorage.setItem(chatStoreKey, JSON.stringify(chatMessages.slice(-40)));
    } catch (e) {}
  }, [chatMessages]);

  React.useEffect(function () {
    if (!chatListRef.current || showScrollButton) return;
    chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
  }, [chatMessages, showScrollButton]);

  function isNearChatBottom() {
    if (!chatListRef.current) return true;
    var node = chatListRef.current;
    return node.scrollHeight - node.scrollTop - node.clientHeight < 48;
  }

  function onChatScroll() {
    setShowScrollButton(!isNearChatBottom());
  }

  function updateChatMessage(id, patch) {
    setChatMessages(function (prev) {
      return prev.map(function (m) {
        return m.id === id ? Object.assign({}, m, patch) : m;
      });
    });
  }

  function sendMaxwellChat(text) {
    var msg = (text || chatInput).trim();
    if (!msg || chatSending) return;
    setChatInput("");
    setShowScrollButton(false);
    var now = Date.now();
    var userMsg = { id: now, role: "operator", content: msg };
    var assistantId = now + 1;
    setChatMessages(function (prev) {
      return prev.concat([
        userMsg,
        { id: assistantId, role: "maxwell", content: "", streaming: true },
      ]);
    });
    setChatSending(true);
    fetch("/api/maxwell/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ message: msg, history: chatMessages.slice(-12) }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        if (!r.body || !window.TextDecoder) return r.text();
        var reader = r.body.getReader();
        var decoder = new TextDecoder();
        var acc = "";
        function pump() {
          return reader.read().then(function (result) {
            if (result.done) return acc;
            acc += decoder.decode(result.value, { stream: true });
            updateChatMessage(assistantId, { content: acc || "Receiving...", streaming: true });
            return pump();
          });
        }
        return pump();
      })
      .then(function (text) {
        updateChatMessage(assistantId, {
          content: text || "Maxwell returned an empty response.",
          streaming: false,
        });
      })
      .catch(function (err) {
        updateChatMessage(assistantId, {
          content: "Maxwell-Daemon is unreachable. Check daemon status above, then retry.",
          detail: String(err),
          streaming: false,
          error: true,
        });
      })
      .finally(function () {
        setChatSending(false);
      });
  }

  function onChatKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMaxwellChat();
    }
  }

  function doControl(action) {
    setPendingAction(action);
    setControlling(true);
    setControlStatus(null);
    onControl({ action: action })
      .then(function () {
        setControlStatus({ ok: true, msg: "Requested " + action + "." });
        if (onRefresh) setTimeout(onRefresh, 1000);
      })
      .catch(function (err) {
        setControlStatus({ ok: false, msg: String(err) });
      })
      .finally(function () {
        setControlling(false);
        setPendingAction("");
      });
  }
  return h(
    "div",
    null,
    h("div", { className: "stat-row" },
      h(Stat, { label: "Status", value: status.status || "unknown", sub: status.service_detail || "" }),
      h(Stat, { label: "HTTP", value: status.http_reachable ? "reachable" : "offline", sub: status.http_detail || "" }),
      h(Stat, { label: "Binary", value: status.binary_found ? "found" : "missing", sub: status.binary_path || "not on PATH" }),
      h(Stat, { label: "Contract", value: daemonVersion || "unknown" }),
    ),
    h("div", { style: { display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" } },
      h("button", { className: "btn", onClick: onRefresh, disabled: loading, "aria-label": "Refresh Maxwell status" }, I.refresh(12), loading ? "Refreshing..." : "Refresh"),
      !isRunning ? h("button", { className: "btn", onClick: function () { doControl("start"); }, disabled: controlling, "aria-label": "Start Maxwell daemon" }, "Start Maxwell") : null,
      isRunning ? h("button", { className: "btn", onClick: function () { doControl("stop"); }, disabled: controlling, "aria-label": "Stop Maxwell daemon" }, "Stop Maxwell") : null,
      isRunning ? h("button", { className: "btn", onClick: function () { doControl("restart"); }, disabled: controlling, "aria-label": "Restart Maxwell daemon" }, "Restart Maxwell") : null,
    ),
    controlStatus ? h("div", { style: { padding: "10px 12px", borderRadius: 8, marginBottom: 12, background: controlStatus.ok ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)", color: controlStatus.ok ? "var(--accent-green)" : "var(--accent-red)" } }, controlStatus.msg) : null,
    error ? h("div", { style: { padding: "10px 12px", borderRadius: 8, marginBottom: 12, background: "rgba(248,81,73,0.12)", color: "var(--accent-red)" } }, error) : null,
    h("div", { className: "section" },
      h("div", { className: "section-header" }, h("span", { className: "section-title" }, I.server(14), "Maxwell-Daemon")),
      h("div", { className: "section-body" },
        pendingAction ? h("div", { style: { fontSize: 12, color: "var(--text-muted)", marginBottom: 8 } }, "Working on " + pendingAction + "...") : null,
        status.dashboard_url ? h("a", { href: status.dashboard_url, target: "_blank", rel: "noopener noreferrer", style: { color: "var(--accent-blue)" } }, status.dashboard_url + " ↗") : null,
        !status.binary_found && !status.service_running && !status.http_reachable
          ? h("div", { style: { marginTop: 12, fontSize: 12, color: "var(--text-muted)" } }, "Maxwell-Daemon is not detected on this machine.")
          : null,
      ),
    ),
    h("div", { className: "section maxwell-chat-section" },
      h("div", { className: "section-header" },
        h("span", { className: "section-title" }, I.messageSquare ? I.messageSquare(14) : null, "Maxwell Chat")
      ),
      h("div", { className: "section-body maxwell-chat" },
        h("div", {
          className: "maxwell-chat-messages",
          ref: chatListRef,
          onScroll: onChatScroll,
          "aria-live": "polite",
        },
          chatMessages.length === 0
            ? h("div", { className: "maxwell-chat-empty" },
                status.http_reachable
                  ? "Ask Maxwell for fleet status, recent runner activity, or the next operator command."
                  : "Maxwell-Daemon is unreachable. Chat history is preserved; use Retry after the daemon is reachable."
              )
            : chatMessages.map(function (msg) {
                return h("div", {
                  key: msg.id,
                  className: "maxwell-chat-bubble " + msg.role + (msg.error ? " error" : ""),
                },
                  msg.content || (msg.streaming ? "Streaming..." : ""),
                  msg.streaming ? h("span", { style: { color: "var(--text-muted)" } }, " ▌") : null
                );
              })
        ),
        showScrollButton ? h("button", {
          "aria-label": "Scroll to bottom of chat",
          className: "btn maxwell-scroll-button",
          onClick: function () {
            setShowScrollButton(false);
            if (chatListRef.current) chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
          },
        }, "Latest") : null,
        h("div", { className: "maxwell-quick-actions", "aria-label": "Maxwell quick actions" },
          ["status", "summarize last hour", "which runners are blocked?"].map(function (chip) {
            return h("button", {
              key: chip,
              className: "btn",
              type: "button",
              onClick: function () { sendMaxwellChat(chip); },
              disabled: chatSending,
            }, chip);
          }),
          !status.http_reachable ? h("button", {
            className: "btn btn-blue",
            type: "button",
            onClick: function () {
              if (onRefresh) onRefresh();
              fetchTasks();
              fetchVersion();
            },
          }, "Retry") : null
        ),
        h("div", { className: "maxwell-composer" },
          h("textarea", {
            value: chatInput,
            onChange: function (e) { setChatInput(e.target.value); },
            onKeyDown: onChatKeyDown,
            placeholder: status.http_reachable ? "Message Maxwell..." : "Daemon unreachable; retry before sending commands",
            rows: 1,
            disabled: chatSending || !status.http_reachable,
            style: {
              width: "100%",
              boxSizing: "border-box",
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--bg-tertiary)",
              color: "var(--text-primary)",
              padding: "10px 12px",
              fontFamily: "inherit",
              fontSize: 13,
            },
          }),
          h("button", {
            className: "btn btn-blue",
            type: "button",
            onClick: function () { sendMaxwellChat(); },
            disabled: chatSending || !chatInput.trim() || !status.http_reachable,
          }, chatSending ? "Sending..." : "Send")
        )
      )
    ),
    h("div", { className: "section" },
      h("div", { className: "section-header" },
        h("span", { className: "section-title" }, I.list ? I.list(14) : null, "Recent Tasks")
      ),
      h("div", { className: "section-body" },
        tasksLoading ? h("div", { style: { color: "var(--text-muted)", fontSize: 12 } }, "Loading tasks…") :
        !status.http_reachable ? h("div", { style: { fontSize: 12, color: "var(--text-muted)" } }, "Maxwell-Daemon offline — no task history") :
        tasks.length === 0 ? h("div", { style: { fontSize: 12, color: "var(--text-muted)" } }, "No tasks yet") :
        h("table", { className: "data-table" },
          h("thead", null, h("tr", null,
            h("th", null, "ID"), h("th", null, "Status"), h("th", null, "Repo"), h("th", null, "Created")
          )),
          h("tbody", null, tasks.map(function(t) {
            return h("tr", { key: t.id },
              h("td", null, (t.id||"").slice(0,8)),
              h("td", null, t.status || "—"),
              h("td", null, t.repo || "—"),
              h("td", null, t.created_at ? t.created_at.slice(0,16).replace("T"," ") : "—")
            );
          }))
        )
      )
    ),
  );
}

/*
function MaxwellTab(p) {
  var status = p.status || {};
  var loading = p.loading;
  var error = p.error;
  var onRefresh = p.onRefresh;
  var onControl = p.onControl;

  var cs = React.useState(null);
  var controlStatus = cs[0],
    setControlStatus = cs[1];
  var cl = React.useState(false);
  var controlling = cl[0],
    setControlling = cl[1];
  var ca = React.useState("");
  var pendingAction = ca[0],
    setPendingAction = ca[1];
  var confirm = React.useState(false);
  var showConfirm = confirm[0],
    setShowConfirm = confirm[1];

  var isRunning = status.status === "running";
  var statusColor = isRunning
    ? "var(--accent-green)"
    : "var(--accent-red)";
  var statusBg = isRunning
    ? "rgba(63,185,80,0.12)"
    : "rgba(248,81,73,0.12)";

  function initiateControl(action) {
    setPendingAction(action);
    setShowConfirm(true);
  }

  function doControl() {
    setControlling(true);
    onControl({
      action: pendingAction,
      approved_by: (principal && principal.name) || "anonymous",
    })
      .then(function (d) {
        setControlStatus({ ok: true, msg: d.status || "Done" });
        onRefresh();
      })
      .catch(function (e) {
        setControlStatus({
          ok: false,
          msg: e.message || "Control failed",
        });
      })
      .finally(function () {
        setControlling(false);
        setShowConfirm(false);
        setPendingAction("");
      });
  }

  return h(
    "div",
    null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Ready",
        value: summary.ready || 0,
        sub: "providers available",
      }),
      h(Stat, {
        label: "Not ready",
        value: summary.not_ready || 0,
        sub: "need setup",
      }),
      h(Stat, {
        label: "Total",
        value: summary.total || 0,
        sub: "providers probed",
        label: "Status",
        value: status.status || "unknown",
        sub: status.service_detail || "",
      }),
      h(Stat, {
        label: "HTTP",
        value: status.http_reachable ? "reachable" : "offline",
        sub: status.http_detail || "",
      }),
      h(Stat, {
        label: "Binary",
        value: status.binary_found ? "found" : "missing",
        sub: status.binary_path || "not on PATH",
      }),
    ),
    h(
      "div",
      {
        style: {
          display: "flex",
          gap: 8,
          marginBottom: 16,
          alignItems: "center",
        },
      },
      h(
        "button",
        { className: "btn", onClick: onRefresh, disabled: loading },
        I.refresh(12),
        loading ? "Probing…" : "Re-probe",
      ),
      h(
        "span",
        { style: { fontSize: 12, color: "var(--text-muted)" } },
        "Probes run locally. No secrets are shown.",
      ),
        loading ? "Probing\u2026" : "Refresh",
      ),
      !isRunning
        ? h(
            "button",
            {
              className: "btn",
              onClick: function () {
                initiateControl("start");
              },
              disabled: controlling,
            },
            "Start Maxwell",
          )
        : null,
      isRunning
        ? h(
            "button",
            {
              className: "btn",
              onClick: function () {
                initiateControl("stop");
              },
              disabled: controlling,
            },
            "Stop Maxwell",
          )
        : null,
      isRunning
        ? h(
            "button",
            {
              className: "btn",
              onClick: function () {
                initiateControl("restart");
              },
              disabled: controlling,
            },
            "Restart Maxwell",
          )
        : null,
    ),
    error
      ? h(
          "div",
          {
            style: {
              padding: "10px 12px",
              borderRadius: 8,
              background: "rgba(248,81,73,0.12)",
              color: "var(--accent-red)",
              fontSize: 12,
              marginBottom: 12,
            },
          },
          error,
        )
      : null,
    h(
      "div",
      {
        style: {
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
          gap: 12,
        },
      },
      probes.map(function (probe) {
        return h(
          "div",
          {
            key: probe.id,
            style: {
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 8,
    controlStatus
      ? h(
          "div",
          {
            style: {
              padding: "10px 12px",
              borderRadius: 8,
              fontSize: 12,
              marginBottom: 12,
              background: controlStatus.ok
                ? "rgba(63,185,80,0.12)"
                : "rgba(248,81,73,0.12)",
              color: controlStatus.ok
                ? "var(--accent-green)"
                : "var(--accent-red)",
            },
          },
          controlStatus.msg,
        )
      : null,
    showConfirm
      ? h(
          "div",
          {
            style: {
              padding: "12px 16px",
              borderRadius: 8,
              background: "rgba(210,153,34,0.1)",
              border: "1px solid var(--border)",
              marginBottom: 12,
            },
          },
          h(
            "div",
            {
              style: {
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                fontSize: 13,
                fontWeight: 600,
                marginBottom: 8,
                color: "var(--accent-yellow)",
              },
            },
            "Confirm: " + pendingAction + " Maxwell-Daemon?",
          ),
          h(
            "div",
            { style: { display: "flex", gap: 8 } },
            h(
              "button",
              {
                className: "btn",
                onClick: doControl,
                disabled: controlling,
              },
              controlling ? "Working\u2026" : "Confirm " + pendingAction,
            ),
            h(
              "button",
              {
                className: "btn",
                style: { opacity: 0.7 },
                onClick: function () {
                  setShowConfirm(false);
                },
              },
              "Cancel",
            ),
          ),
        )
      : null,
    h(
      "div",
      { className: "section" },
      h(
        "div",
        { className: "section-header" },
        h(
          "span",
          { className: "section-title", style: { color: statusColor } },
          I.server(14),
          "Maxwell-Daemon",
        ),
      ),
      h(
        "div",
        { className: "section-body" },
        h(
          "div",
          {
            style: {
              display: "flex",
              gap: 12,
              flexWrap: "wrap",
              marginBottom: 12,
            },
          },
          h(
            "span",
            {
              className: "section-badge",
              style: {
                background: statusBg,
                color: statusColor,
                fontSize: 13,
                padding: "4px 12px",
              },
            },
            status.status || "unknown",
          ),
        ),
        status.dashboard_url
          ? h(
              "div",
              { style: { marginBottom: 8 } },
              h(
                "span",
                {
                  style: { fontSize: 12, color: "var(--text-secondary)" },
                },
                "Dashboard URL: ",
              ),
              h(
                "a",
                {
                  href: status.dashboard_url,
                  target: "_blank",
                  rel: "noopener noreferrer",
                  style: { fontSize: 12, color: "var(--text-secondary)" },
                },
                status.dashboard_url + " \u2197",
              ),
            )
          : null,
        status.deep_links
          ? h(
              "div",
              null,
              h(
                "div",
                {
                  style: {
                    fontSize: 12,
                    color: "var(--text-secondary)",
                    marginBottom: 6,
                  },
                },
                "Deep links:",
              ),
              Object.entries(status.deep_links || {}).map(
                function (entry) {
                  var k = entry[0],
                    v = entry[1];
                  return h(
                    "div",
                    { key: k, style: { fontSize: 12, marginBottom: 4 } },
                    h(
                      "span",
                      {
                        style: {
                          color: "var(--text-muted)",
                          minWidth: 80,
                          display: "inline-block",
                        },
                      },
                      k + ":",
                    ),
                    v.startsWith("http")
                      ? h(
                          "a",
                          {
                            href: v,
                            target: "_blank",
                            rel: "noopener noreferrer",
                            style: { color: "var(--text-secondary)" },
                          },
                          v + " \u2197",
                        )
                      : h(
                          "code",
                          {
                            style: {
                              fontSize: 11,
                              color: "var(--text-secondary)",
                              background: "var(--bg-secondary)",
                              padding: "2px 6px",
                              borderRadius: 4,
                            },
                          },
                          v,
                        ),
                  );
                },
              ),
            )
          : null,
        !status.binary_found &&
          !status.service_running &&
          !status.http_reachable
          ? h(
              "div",
              {
                style: {
                  marginTop: 12,
                  padding: "10px 12px",
                  borderRadius: 8,
                  background: "rgba(139,148,158,0.08)",
                  fontSize: 12,
                  color: "var(--text-muted)",
                },
              },
              "Maxwell-Daemon is not detected on this machine. If installed elsewhere, set MAXWELL_URL to point to it.",
            )
          : null,
      ),
    ),
  );
}

*/

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
              color: "#fff",
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

/*
function DashboardHelp(p) {
  var currentTab = p.currentTab || "";
  var open = React.useState(false);
  var isOpen = open[0],
    setIsOpen = open[1];
  var msgs = React.useState([]);
  var messages = msgs[0],
    setMessages = msgs[1];
  var inp = React.useState("");
  var input = inp[0],
    setInput = inp[1];
  var loading = React.useState(false);
  var isLoading = loading[0],
    setIsLoading = loading[1];

  function sendMessage() {
    var q = input.trim();
    if (!q) return;
    setInput("");
    setMessages(function (prev) {
      return prev.concat({ role: "user", content: q });
    });
    setIsLoading(true);
    fetch("/api/help/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ question: q, current_tab: currentTab }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setMessages(function (prev) {
          return prev.concat({
            role: "assistant",
            content: d.answer || "No answer available.",
            source: d.source,
          });
        });
        setIsLoading(false);
      })
      .catch(function () {
        setMessages(function (prev) {
          return prev.concat({
            role: "assistant",
            content: "Help service unavailable.",
            source: "error",
          });
        });
        setIsLoading(false);
      });
  }

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
            style: {
              width: 48,
              height: 48,
              borderRadius: "50%",
              background: "var(--accent-purple, #886ce4)",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontSize: 20,
              boxShadow: "0 2px 12px rgba(0,0,0,0.3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            },
          },
          "?",
        )
      : null,
    isOpen
      ? h(
          "div",
          {
            style: {
              width: 340,
              height: 440,
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              display: "flex",
              flexDirection: "column",
              boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
              overflow: "hidden",
            },
          },
          h(
            "div",
            {
              style: {
                padding: "12px 16px",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                background: "var(--bg-secondary)",
              },
            },
            h(
              "span",
              { style: { fontWeight: 600, fontSize: 14 } },
              probe.label,
            ),
            h(
              "span",
              {
                className: "section-badge",
                style: {
                  background: statusBg(probe.status),
                  color: statusColor(probe.status),
                },
              },
              statusLabel(probe.status),
              "Dashboard Help",
            ),
            h(
              "button",
              {
                onClick: function () {
                  setIsOpen(false);
                },
                style: {
                  background: "none",
                  border: "none",
                  color: "var(--text-muted)",
                  cursor: "pointer",
                  fontSize: 18,
                  lineHeight: 1,
                },
              },
              "\u00d7",
            ),
          ),
          h(
            "div",
            { style: { fontSize: 12, color: "var(--text-secondary)" } },
            probe.detail || "",
            {
              style: { flex: 1, overflow: "auto", padding: "12px 14px" },
            },
            messages.length === 0
              ? h(
                  "div",
                  {
                    style: {
                      color: "var(--text-muted)",
                      fontSize: 12,
                      textAlign: "center",
                      marginTop: 40,
                    },
                  },
                  "Ask me anything about the dashboard!",
                )
              : messages.map(function (msg, i) {
                  return h(
                    "div",
                    {
                      key: i,
                      style: {
                        marginBottom: 10,
                        padding: "8px 10px",
                        borderRadius: 8,
                        fontSize: 12,
                        background:
                          msg.role === "user"
                            ? "rgba(136,108,228,0.12)"
                            : "var(--bg-secondary)",
                        color:
                          msg.role === "user"
                            ? "var(--accent-purple)"
                            : "var(--text-primary)",
                        textAlign: msg.role === "user" ? "right" : "left",
                      },
                    },
                    msg.content,
                  );
                }),
            isLoading
              ? h(
                  "div",
                  {
                    style: {
                      color: "var(--text-muted)",
                      fontSize: 12,
                      fontStyle: "italic",
                    },
                  },
                  "Thinking\u2026",
                )
              : null,
          ),
          h(
            "div",
            {
              style: {
                display: "flex",
                gap: 12,
                fontSize: 11,
                color: "var(--text-muted)",
              },
            },
            h(
              "span",
              null,
              h(
                "span",
                {
                  style: {
                    color: probe.installed
                      ? "var(--accent-green)"
                      : "var(--accent-red)",
                  },
                },
                probe.installed ? "✓" : "✗",
              ),
              " Installed",
            ),
            h(
              "span",
              null,
              h(
                "span",
                {
                  style: {
                    color: probe.authenticated
                      ? "var(--accent-green)"
                      : "var(--accent-yellow)",
                  },
                },
                probe.authenticated ? "✓" : "✗",
              ),
              " Auth",
            ),
            h(
              "span",
              null,
              h(
                "span",
                {
                  style: {
                    color: probe.usable
                      ? "var(--accent-green)"
                      : "var(--accent-red)",
                  },
                },
                probe.usable ? "✓" : "✗",
              ),
              " Usable",
            ),
          ),
          probe.config_source && probe.config_source !== "unavailable"
            ? h(
                "div",
                { style: { fontSize: 11, color: "var(--text-muted)" } },
                "Config: ",
                probe.config_source,
              )
            : null,
          !probe.usable && probe.setup_hint
            ? h(
                "div",
                {
                  style: {
                    fontSize: 11,
                    color: "var(--accent-yellow)",
                    background: "rgba(210,153,34,0.08)",
                    borderRadius: 6,
                    padding: "4px 8px",
                  },
                },
                "Setup: ",
                probe.setup_hint,
              )
            : null,
          probe.docs_url
            ? h(
                "a",
                {
                  href: probe.docs_url,
                  target: "_blank",
                  rel: "noopener noreferrer",
                  style: { fontSize: 11, color: "var(--text-secondary)" },
                },
                "Docs ↗",
              )
            : null,
        );
      }),
    ),
                padding: "10px 12px",
                borderTop: "1px solid var(--border)",
                display: "flex",
                gap: 8,
              },
            },
            h("input", {
              type: "text",
              value: input,
              placeholder: "Ask about the dashboard\u2026",
              onChange: function (e) {
                setInput(e.target.value);
              },
              onKeyDown: function (e) {
                if (e.key === "Enter" && !isLoading) sendMessage();
              },
              style: {
                flex: 1,
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "6px 10px",
                fontSize: 12,
              },
            }),
            h(
              "button",
              {
                onClick: sendMessage,
                disabled: isLoading || !input.trim(),
                className: "btn",
                style: { padding: "6px 12px", fontSize: 12 },
              },
              "Ask",
            ),
          ),
        )
      : null,
  );
}

*/

function FleetOrchestrationTab(p) {
  var data = p.data || {};
  var loading = p.loading;
  var error = p.error;
  var onRefresh = p.onRefresh;
  var onDispatch = p.onDispatch;
  var onDeploy = p.onDeploy;

  var machines = data.machines || [];
  var auditLog = data.audit_log || [];

  // Dispatch modal state
  var dms = React.useState(false);
  var dispatchModalOpen = dms[0],
    setDispatchModalOpen = dms[1];
  var drs = React.useState("");
  var dispatchRepo = drs[0],
    setDispatchRepo = drs[1];
  var dws = React.useState("");
  var dispatchWorkflow = dws[0],
    setDispatchWorkflow = dws[1];
  var dbrs = React.useState("main");
  var dispatchBranch = dbrs[0],
    setDispatchBranch = dbrs[1];
  var dmts = React.useState("");
  var dispatchMachineTarget = dmts[0],
    setDispatchMachineTarget = dmts[1];
  var dls = React.useState(false);
  var dispatchLoading = dls[0],
    setDispatchLoading = dls[1];
  var des = React.useState(null);
  var dispatchError = des[0],
    setDispatchError = des[1];
  var dss2 = React.useState(null);
  var dispatchSuccess = dss2[0],
    setDispatchSuccess = dss2[1];

  // Deploy section state
  var dpm = React.useState("");
  var deployMachine = dpm[0],
    setDeployMachine = dpm[1];
  var dpa = React.useState("restart_runner");
  var deployAction = dpa[0],
    setDeployAction = dpa[1];
  var dpc = React.useState(false);
  var deployConfirm = dpc[0],
    setDeployConfirm = dpc[1];
  var dpls = React.useState(false);
  var deployLoading = dpls[0],
    setDeployLoading = dpls[1];
  var dpes = React.useState(null);
  var deployError = dpes[0],
    setDeployError = dpes[1];
  var dpss = React.useState(null);
  var deploySuccess = dpss[0],
    setDeploySuccess = dpss[1];
  var machineSortState = React.useState({ key: "machine", dir: "asc" });
  var machineSort = machineSortState[0],
    setMachineSort = machineSortState[1];
  var machineAccessors = {
    machine: function (m) {
      return m.display_name || m.name;
    },
    role: function (m) {
      return m.role || "node";
    },
    status: function (m) {
      return m.online ? 1 : 0;
    },
    runners: function (m) {
      return m.runner_count || 0;
    },
    busy: function (m) {
      return m.busy_runners || 0;
    },
    cpu: function (m) {
      return m.cpu_percent || 0;
    },
    memory: function (m) {
      return m.memory_percent || 0;
    },
    lastPing: function (m) {
      return m.last_ping || "";
    },
  };
  var sortedMachines = sortRows(machines, machineSort, machineAccessors);

  function machineStatusColor(online) {
    return online ? "var(--accent-green)" : "var(--accent-red)";
  }
  function machineStatusBg(online) {
    return online ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)";
  }

  function handleDispatch() {
    if (!dispatchRepo || !dispatchWorkflow) {
      setDispatchError("Repo and workflow are required.");
      return;
    }
    setDispatchLoading(true);
    setDispatchError(null);
    setDispatchSuccess(null);
    onDispatch({
      repo: dispatchRepo,
      workflow: dispatchWorkflow,
      ref: dispatchBranch || "main",
      machine_target: dispatchMachineTarget,
      approved_by: (principal && principal.name) || "anonymous",
    })
      .then(function (d) {
        setDispatchSuccess("Dispatched! audit_id=" + (d.audit_id || ""));
        setDispatchLoading(false);
        setDispatchModalOpen(false);
        onRefresh();
      })
      .catch(function (e) {
        setDispatchError((e && e.message) || "Dispatch failed.");
        setDispatchLoading(false);
      });
  }

  function handleDeploy() {
    if (!deployMachine) {
      setDeployError("Select a machine.");
      return;
    }
    if (!deployConfirm) {
      setDeployError("Check the confirmation box before deploying.");
      return;
    }
    setDeployLoading(true);
    setDeployError(null);
    setDeploySuccess(null);
    onDeploy({
      machine: deployMachine,
      action: deployAction,
      confirmed: true,
      requested_by: (principal && principal.name) || "anonymous",
    })
      .then(function (d) {
        setDeploySuccess(d.message || "Deployed successfully.");
        setDeployLoading(false);
        setDeployConfirm(false);
        onRefresh();
      })
      .catch(function (e) {
        setDeployError((e && e.message) || "Deploy failed.");
        setDeployLoading(false);
      });
  }

  var onlineCount = data.online_count || 0;
  var totalCount = data.total_count || machines.length;

  return h(
    "div",
    null,
    // Stats row
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "Online",
        value: onlineCount,
        sub: "machines reachable",
      }),
      h(Stat, {
        label: "Total",
        value: totalCount,
        sub: "fleet machines",
      }),
      h(Stat, {
        label: "Offline",
        value: totalCount - onlineCount,
        sub: "unreachable",
      }),
    ),
    // Toolbar
    h(
      "div",
      {
        style: {
          display: "flex",
          gap: 8,
          marginBottom: 16,
          alignItems: "center",
          flexWrap: "wrap",
        },
      },
      h(
        "button",
        {
          className: "btn",
          onClick: onRefresh,
          disabled: loading,
        },
        I.refresh(12),
        loading ? "Loading…" : "Refresh",
      ),
      h(
        "button",
        {
          className: "btn",
          onClick: function () {
            setDispatchModalOpen(true);
            setDispatchError(null);
            setDispatchSuccess(null);
          },
        },
        I.play(12),
        "Dispatch Workflow",
      ),
    ),
    // Error
    error
      ? h(
          "div",
          {
            style: {
              padding: "10px 12px",
              borderRadius: 8,
              background: "rgba(248,81,73,0.12)",
              color: "var(--accent-red)",
              fontSize: 12,
              marginBottom: 12,
            },
          },
          error,
        )
      : null,
    // Machines table
    h(
      "div",
      {
        style: {
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          marginBottom: 20,
          overflow: "hidden",
        },
      },
      h(
        "div",
        {
          style: {
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            fontWeight: 600,
            fontSize: 13,
          },
        },
        "Fleet Machines",
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
              label: "Machine",
              sortKey: "machine",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Role",
              sortKey: "role",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Status",
              sortKey: "status",
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
              label: "Busy",
              sortKey: "busy",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "CPU %",
              sortKey: "cpu",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Mem %",
              sortKey: "memory",
              sort: machineSort,
              setSort: setMachineSort,
            }),
            h(SortTh, {
              label: "Last Ping",
              sortKey: "lastPing",
              sort: machineSort,
              setSort: setMachineSort,
            }),
          ),
        ),
        h(
          "tbody",
          null,
          machines.length === 0
            ? h(
                "tr",
                null,
                h(
                  "td",
                  {
                    colSpan: 8,
                    style: {
                      textAlign: "center",
                      color: "var(--text-muted)",
                      padding: 20,
                    },
                  },
                  loading ? "Loading machines…" : "No machines found.",
                ),
              )
            : sortedMachines.map(function (m) {
                return h(
                  "tr",
                  { key: m.name },
                  h(
                    "td",
                    null,
                    h(
                      "span",
                      { style: { fontWeight: 600 } },
                      m.display_name || m.name,
                    ),
                    m.dashboard_url
                      ? h(
                          "a",
                          {
                            href: m.dashboard_url,
                            target: "_blank",
                            rel: "noopener noreferrer",
                            style: {
                              marginLeft: 6,
                              fontSize: 11,
                              color: "var(--text-muted)",
                            },
                          },
                          "↗",
                        )
                      : null,
                  ),
                  h(
                    "td",
                    {
                      style: {
                        color: "var(--text-secondary)",
                        fontSize: 12,
                      },
                    },
                    m.role || "node",
                  ),
                  h(
                    "td",
                    null,
                    h(
                      "span",
                      {
                        className: "section-badge",
                        style: {
                          background: machineStatusBg(m.online),
                          color: machineStatusColor(m.online),
                        },
                      },
                      m.online ? "Online" : "Offline",
                    ),
                  ),
                  h(
                    "td",
                    null,
                    m.runner_count != null ? m.runner_count : "—",
                  ),
                  h(
                    "td",
                    null,
                    m.busy_runners != null ? m.busy_runners : "—",
                  ),
                  h(
                    "td",
                    null,
                    m.cpu_percent != null
                      ? m.cpu_percent.toFixed(0) + "%"
                      : "—",
                  ),
                  h(
                    "td",
                    null,
                    m.memory_percent != null
                      ? m.memory_percent.toFixed(0) + "%"
                      : "—",
                  ),
                  h(
                    "td",
                    {
                      style: {
                        fontSize: 11,
                        color: "var(--text-muted)",
                      },
                    },
                    m.last_ping
                      ? new Date(m.last_ping).toLocaleTimeString()
                      : "—",
                  ),
                );
              }),
        ),
      ),
    ),
    // Deploy Action section
    h(
      "div",
      {
        style: {
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: 16,
          marginBottom: 20,
        },
      },
      h(
        "div",
        {
          style: {
            fontWeight: 600,
            fontSize: 13,
            marginBottom: 12,
          },
        },
        "Deploy Action",
      ),
      h(
        "div",
        {
          style: {
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
            marginBottom: 12,
          },
        },
        h(
          "div",
          null,
          h(
            "label",
            {
              style: {
                display: "block",
                fontSize: 12,
                marginBottom: 4,
                color: "var(--text-secondary)",
              },
            },
            "Target Machine",
          ),
          h(
            "select",
            {
              className: "input",
              style: { width: "100%" },
              value: deployMachine,
              onChange: function (e) {
                setDeployMachine(e.target.value);
              },
            },
            h("option", { value: "" }, "— select machine —"),
            machines.map(function (m) {
              return h(
                "option",
                { key: m.name, value: m.name },
                m.display_name || m.name,
              );
            }),
          ),
        ),
        h(
          "div",
          null,
          h(
            "label",
            {
              style: {
                display: "block",
                fontSize: 12,
                marginBottom: 4,
                color: "var(--text-secondary)",
              },
            },
            "Action",
          ),
          h(
            "select",
            {
              className: "input",
              style: { width: "100%" },
              value: deployAction,
              onChange: function (e) {
                setDeployAction(e.target.value);
              },
            },
            h("option", { value: "restart_runner" }, "Restart Runner"),
            h("option", { value: "sync_workflows" }, "Sync Workflows"),
            h("option", { value: "update_config" }, "Update Config"),
          ),
        ),
      ),
      h(
        "label",
        {
          style: {
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 12,
            marginBottom: 12,
            cursor: "pointer",
          },
        },
        h("input", {
          type: "checkbox",
          checked: deployConfirm,
          onChange: function (e) {
            setDeployConfirm(e.target.checked);
          },
        }),
        "I confirm this action against the selected machine",
      ),
      deployError
        ? h(
            "div",
            {
              style: {
                color: "var(--accent-red)",
                fontSize: 12,
                marginBottom: 8,
              },
            },
            deployError,
          )
        : null,
      deploySuccess
        ? h(
            "div",
            {
              style: {
                color: "var(--accent-green)",
                fontSize: 12,
                marginBottom: 8,
              },
            },
            deploySuccess,
          )
        : null,
      h(
        "button",
        {
          className: "btn",
          onClick: handleDeploy,
          disabled: deployLoading || !deployConfirm,
        },
        deployLoading ? "Deploying…" : "Deploy",
      ),
    ),
    // Audit log
    h(
      "div",
      {
        style: {
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          overflow: "hidden",
        },
      },
      h(
        "div",
        {
          style: {
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            fontWeight: 600,
            fontSize: 13,
          },
        },
        "Orchestration Audit Log (last 10)",
      ),
      auditLog.length === 0
        ? h(
            "div",
            {
              style: {
                padding: 20,
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: 12,
              },
            },
            "No audit entries yet.",
          )
        : h(
            "table",
            { className: "data-table", style: { width: "100%" } },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h("th", null, "Time"),
                h("th", null, "Type"),
                h("th", null, "Target"),
                h("th", null, "Action"),
                h("th", null, "By"),
                h("th", null, "Decision"),
              ),
            ),
            h(
              "tbody",
              null,
              auditLog.map(function (entry, idx) {
                return h(
                  "tr",
                  { key: entry.audit_id || entry.event_id || idx },
                  h(
                    "td",
                    {
                      style: {
                        fontSize: 11,
                        color: "var(--text-muted)",
                      },
                    },
                    entry.recorded_at
                      ? new Date(entry.recorded_at).toLocaleTimeString()
                      : "—",
                  ),
                  h(
                    "td",
                    {
                      style: { fontSize: 12 },
                    },
                    entry.orchestration_type || entry.action || "—",
                  ),
                  h(
                    "td",
                    { style: { fontSize: 12 } },
                    entry.machine_target ||
                      entry.machine ||
                      entry.target ||
                      "—",
                  ),
                  h(
                    "td",
                    { style: { fontSize: 12 } },
                    entry.deploy_action ||
                      entry.workflow ||
                      entry.action ||
                      "—",
                  ),
                  h(
                    "td",
                    {
                      style: {
                        fontSize: 12,
                        color: "var(--text-secondary)",
                      },
                    },
                    entry.requested_by || "—",
                  ),
                  h(
                    "td",
                    null,
                    h(
                      "span",
                      {
                        className: "section-badge",
                        style: {
                          background:
                            entry.decision === "accepted"
                              ? "rgba(63,185,80,0.12)"
                              : "rgba(248,81,73,0.12)",
                          color:
                            entry.decision === "accepted"
                              ? "var(--accent-green)"
                              : "var(--accent-red)",
                        },
                      },
                      entry.decision || "—",
                    ),
                  ),
                );
              }),
            ),
          ),
    ),
    // Dispatch Workflow Modal
    dispatchModalOpen
      ? h(
          "div",
          {
            style: {
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.5)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1000,
            },
            onClick: function (e) {
              if (e.target === e.currentTarget)
                setDispatchModalOpen(false);
            },
          },
          h(
            "div",
            {
              style: {
                background: "var(--bg-primary)",
                border: "1px solid var(--border)",
                borderRadius: 12,
                padding: 24,
                width: 480,
                maxWidth: "90vw",
              },
            },
            h(
              "div",
              {
                style: {
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 20,
                },
              },
              h(
                "span",
                { style: { fontWeight: 700, fontSize: 15 } },
                "Dispatch Workflow",
              ),
              h(
                "button",
                {
                  className: "btn",
                  onClick: function () {
                    setDispatchModalOpen(false);
                  },
                  style: { padding: "2px 8px" },
                },
                "✕",
              ),
            ),
            h(
              "div",
              {
                style: {
                  display: "flex",
                  flexDirection: "column",
                  gap: 12,
                },
              },
              h(
                "div",
                null,
                h(
                  "label",
                  {
                    style: {
                      display: "block",
                      fontSize: 12,
                      marginBottom: 4,
                      color: "var(--text-secondary)",
                    },
                  },
                  "Repository",
                ),
                h("input", {
                  className: "input",
                  style: { width: "100%" },
                  placeholder: "e.g. Repository_Management",
                  value: dispatchRepo,
                  onChange: function (e) {
                    setDispatchRepo(e.target.value);
                  },
                }),
              ),
              h(
                "div",
                null,
                h(
                  "label",
                  {
                    style: {
                      display: "block",
                      fontSize: 12,
                      marginBottom: 4,
                      color: "var(--text-secondary)",
                    },
                  },
                  "Workflow file",
                ),
                h("input", {
                  className: "input",
                  style: { width: "100%" },
                  placeholder: "e.g. ci-standard.yml",
                  value: dispatchWorkflow,
                  onChange: function (e) {
                    setDispatchWorkflow(e.target.value);
                  },
                }),
              ),
              h(
                "div",
                null,
                h(
                  "label",
                  {
                    style: {
                      display: "block",
                      fontSize: 12,
                      marginBottom: 4,
                      color: "var(--text-secondary)",
                    },
                  },
                  "Branch / ref",
                ),
                h("input", {
                  className: "input",
                  style: { width: "100%" },
                  placeholder: "main",
                  value: dispatchBranch,
                  onChange: function (e) {
                    setDispatchBranch(e.target.value);
                  },
                }),
              ),
              h(
                "div",
                null,
                h(
                  "label",
                  {
                    style: {
                      display: "block",
                      fontSize: 12,
                      marginBottom: 4,
                      color: "var(--text-secondary)",
                    },
                  },
                  "Machine target (optional)",
                ),
                h(
                  "select",
                  {
                    className: "input",
                    style: { width: "100%" },
                    value: dispatchMachineTarget,
                    onChange: function (e) {
                      setDispatchMachineTarget(e.target.value);
                    },
                  },
                  h("option", { value: "" }, "— any machine —"),
                  machines.map(function (m) {
                    return h(
                      "option",
                      { key: m.name, value: m.name },
                      m.display_name || m.name,
                    );
                  }),
                ),
              ),
            ),
            dispatchError
              ? h(
                  "div",
                  {
                    style: {
                      color: "var(--accent-red)",
                      fontSize: 12,
                      marginTop: 12,
                    },
                  },
                  dispatchError,
                )
              : null,
            h(
              "div",
              {
                style: {
                  display: "flex",
                  gap: 8,
                  marginTop: 20,
                  justifyContent: "flex-end",
                },
              },
              h(
                "button",
                {
                  className: "btn",
                  onClick: function () {
                    setDispatchModalOpen(false);
                  },
                },
                "Cancel",
              ),
              h(
                "button",
                {
                  className: "btn btn-primary",
                  onClick: handleDispatch,
                  disabled: dispatchLoading,
                },
                dispatchLoading ? "Dispatching…" : "Dispatch",
              ),
            ),
          ),
        )
      : null,
  );
}


function AssessmentsTab(props) {
  var repos = props.repos || [];
  var scores = props.scores || [];
  var loading = props.loading;
  var error = props.error;
  var onDispatch = props.onDispatch;
  var onRefresh = props.onRefresh;
  var rrs = React.useState("");
  var selRepo = rrs[0],
    setSelRepo = rrs[1];
  var pvs = React.useState("jules_api");
  var selProvider = pvs[0],
    setSelProvider = pvs[1];
  var cms = React.useState(false);
  var showConfirm = cms[0],
    setShowConfirm = cms[1];
  var dss = React.useState(null);
  var dispatchStatus = dss[0],
    setDispatchStatus = dss[1];
  function grouped() {
    var m = {};
    scores.forEach(function (s) {
      var k = s.repo || "unknown";
      if (!m[k]) m[k] = [];
      m[k].push(s);
    });
    return m;
  }
  function doDispatch() {
    setShowConfirm(false);
    setDispatchStatus("dispatching");
    onDispatch({ repository: selRepo, provider: selProvider })
      .then(function () {
        setDispatchStatus("ok");
      })
      .catch(function () {
        setDispatchStatus("error");
      });
  }
  function formatAssessmentScore(e) {
    var value = e && e.score;
    if (value == null) return "\u2014";
    if (typeof value === "number") {
      return value <= 1 ? Math.round(value * 100) + "%" : value;
    }
    return value;
  }
  function formatAssessmentDate(e) {
    var value = e && e.date;
    if (!value) return "\u2014";
    if (typeof value === "number") {
      return new Date(value * 1000).toLocaleDateString();
    }
    return String(value).slice(0, 10);
  }
  var g = grouped();
  return h(
    "div",
    { style: { padding: 20 } },
    h("div", { className: "section-header" }, I.activity(14), "Assessments"),
    error ? h("div", { className: "error-banner" }, error) : null,
    h(
      "div",
      {
        style: {
          display: "flex",
          gap: 10,
          marginBottom: 16,
          flexWrap: "wrap",
          alignItems: "flex-end",
        },
      },
      h(
        "div",
        {},
        h(
          "label",
          {
            style: {
              display: "block",
              fontSize: 11,
              color: "var(--text-muted)",
              marginBottom: 4,
            },
          },
          "Repository",
        ),
        h(
          "select",
          {
            value: selRepo,
            onChange: function (e) {
              setSelRepo(e.target.value);
            },
            style: {
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              borderRadius: 4,
              padding: "4px 8px",
              minWidth: 180,
            },
          },
          h("option", { value: "" }, "— pick a repo —"),
          repos.map(function (r) {
            return h(
              "option",
              { key: r.name || r, value: r.name || r },
              r.name || r,
            );
          }),
        ),
      ),
      h(
        "div",
        {},
        h(
          "label",
          {
            style: {
              display: "block",
              fontSize: 11,
              color: "var(--text-muted)",
              marginBottom: 4,
            },
          },
          "Provider",
        ),
        h(
          "select",
          {
            value: selProvider,
            onChange: function (e) {
              setSelProvider(e.target.value);
            },
            style: {
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              borderRadius: 4,
              padding: "4px 8px",
            },
          },
          h("option", { value: "jules_api" }, "Jules"),
          h("option", { value: "codex" }, "Codex"),
          h("option", { value: "claude" }, "Claude"),
        ),
      ),
      h(
        "button",
        {
          className: "action-btn",
          disabled: !selRepo,
          onClick: function () {
            setShowConfirm(true);
          },
        },
        I.activity(14),
        " Run Assessment",
      ),
      h(
        "button",
        {
          className: "action-btn secondary",
          onClick: onRefresh,
          disabled: loading,
        },
        I.refresh(12),
        " Refresh",
      ),
    ),
    dispatchStatus === "ok"
      ? h(
          "div",
          {
            style: {
              color: "var(--accent-green)",
              marginBottom: 12,
              fontSize: 13,
            },
          },
          "Assessment dispatched successfully.",
        )
      : null,
    dispatchStatus === "error"
      ? h(
          "div",
          {
            style: {
              color: "var(--accent-red)",
              marginBottom: 12,
              fontSize: 13,
            },
          },
          "Dispatch failed — check GitHub Actions.",
        )
      : null,
    showConfirm
      ? h(
          "div",
          {
            style: {
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: 16,
              marginBottom: 16,
            },
          },
          h(
            "p",
            { style: { margin: "0 0 12px", fontSize: 13 } },
            "Dispatch assessment of ",
            h("strong", {}, selRepo),
            " via ",
            h("strong", {}, selProvider),
            "?",
          ),
          h(
            "div",
            { style: { display: "flex", gap: 8 } },
            h(
              "button",
              { className: "action-btn", onClick: doDispatch },
              "Confirm",
            ),
            h(
              "button",
              {
                className: "action-btn secondary",
                onClick: function () {
                  setShowConfirm(false);
                },
              },
              "Cancel",
            ),
          ),
        )
      : null,
    loading
      ? h(
          "div",
          { style: { color: "var(--text-muted)", padding: 20 } },
          "Loading scores…",
        )
      : scores.length === 0
        ? h(
            "div",
            { style: { color: "var(--text-muted)", padding: 20 } },
            "No assessment scores found in assessments/ directory.",
          )
        : h(
            "div",
            {},
            Object.keys(g)
              .sort()
              .map(function (repoKey) {
                var entries = g[repoKey];
                return h(
                  "div",
                  { key: repoKey, style: { marginBottom: 20 } },
                  h(
                    "div",
                    {
                      style: {
                        fontWeight: 600,
                        fontSize: 13,
                        marginBottom: 8,
                        color: "var(--text-primary)",
                      },
                    },
                    repoKey,
                  ),
                  h(
                    "div",
                    { className: "assessment-mobile-card-list" },
                    entries.map(function (e, i) {
                      return h(
                        "article",
                        { key: "mobile-" + i, className: "assessment-mobile-card" },
                        h(
                          "div",
                          { className: "assessment-mobile-card-title" },
                          h("span", null, repoKey),
                          h(
                            "span",
                            { className: "assessment-mobile-score" },
                            formatAssessmentScore(e),
                          ),
                        ),
                        h(
                          "div",
                          { className: "assessment-mobile-summary" },
                          e.summary || "No summary captured.",
                        ),
                        h(
                          "div",
                          { className: "assessment-mobile-meta" },
                          h(
                            "span",
                            { className: "assessment-mobile-chip" },
                            e.provider || "provider unknown",
                          ),
                          h(
                            "span",
                            { className: "assessment-mobile-chip" },
                            formatAssessmentDate(e),
                          ),
                        ),
                      );
                    }),
                  ),
                  h(
                    "table",
                    {
                      className: "assessment-desktop-table",
                      style: {
                        width: "100%",
                        borderCollapse: "collapse",
                        fontSize: 12,
                      },
                    },
                    h(
                      "thead",
                      {},
                      h(
                        "tr",
                        {},
                        h(
                          "th",
                          {
                            style: {
                              textAlign: "left",
                              padding: "4px 8px",
                              borderBottom: "1px solid var(--border)",
                              color: "var(--text-muted)",
                            },
                          },
                          "Score",
                        ),
                        h(
                          "th",
                          {
                            style: {
                              textAlign: "left",
                              padding: "4px 8px",
                              borderBottom: "1px solid var(--border)",
                              color: "var(--text-muted)",
                            },
                          },
                          "Provider",
                        ),
                        h(
                          "th",
                          {
                            style: {
                              textAlign: "left",
                              padding: "4px 8px",
                              borderBottom: "1px solid var(--border)",
                              color: "var(--text-muted)",
                            },
                          },
                          "Date",
                        ),
                        h(
                          "th",
                          {
                            style: {
                              textAlign: "left",
                              padding: "4px 8px",
                              borderBottom: "1px solid var(--border)",
                              color: "var(--text-muted)",
                            },
                          },
                          "Summary",
                        ),
                      ),
                    ),
                    h(
                      "tbody",
                      {},
                      entries.map(function (e, i) {
                        var score = formatAssessmentScore(e);
                        var dateStr = formatAssessmentDate(e);
                        return h(
                          "tr",
                          {
                            key: i,
                            style: {
                              borderBottom: "1px solid var(--border)",
                            },
                          },
                          h(
                            "td",
                            {
                              style: {
                                padding: "4px 8px",
                                fontWeight: 600,
                                color: "var(--accent-green)",
                              },
                            },
                            score,
                          ),
                          h(
                            "td",
                            {
                              style: {
                                padding: "4px 8px",
                                color: "var(--text-secondary)",
                              },
                            },
                            e.provider || "—",
                          ),
                          h(
                            "td",
                            {
                              style: {
                                padding: "4px 8px",
                                color: "var(--text-muted)",
                              },
                            },
                            dateStr,
                          ),
                          h(
                            "td",
                            {
                              style: {
                                padding: "4px 8px",
                                color: "var(--text-secondary)",
                                maxWidth: 300,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              },
                            },
                            e.summary || "—",
                          ),
                        );
                      }),
                    ),
                  ),
                );
              }),
          ),
  );
}

function ClineLauncherTab() {
  // Self-contained: this tab manages its own polling + actions so it
  // doesn't bloat the parent state. The launcher's state is on the
  // user's local FS — the dashboard only mirrors it.
  var s = React.useState(null);
  var status = s[0], setStatus = s[1];
  var r = React.useState(null);
  var repos = r[0], setRepos = r[1];
  var l = React.useState(false);
  var loading = l[0], setLoading = l[1];
  var er = React.useState("");
  var error = er[0], setError = er[1];
  var bs = React.useState({});
  var busy = bs[0], setBusy = bs[1];

  function fetchStatus() {
    fetch("/api/agent-launcher/status")
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (j) { setStatus(j); setError(""); })
      .catch(function (e) { setError(String(e)); });
  }

  function fetchRepos() {
    setLoading(true);
    fetch("/api/agent-launcher/repos")
      .then(function (resp) {
        if (resp.status === 503) {
          throw new Error(
            "launcher not installed on this machine"
          );
        }
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (j) { setRepos(j); setError(""); })
      .catch(function (e) { setError(String(e)); })
      .finally(function () { setLoading(false); });
  }

  function action(verb, body) {
    var key = verb + (body && body.agent ? "/" + body.agent : "");
    var nb = Object.assign({}, busy); nb[key] = true; setBusy(nb);
    var opts = { method: "POST" };
    if (body) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    return fetch("/api/agent-launcher/" + verb, opts)
      .then(function (resp) {
        return resp.json().then(function (j) {
          if (!resp.ok) throw new Error(j.detail || "HTTP " + resp.status);
          return j;
        });
      })
      .then(function () { fetchStatus(); })
      .catch(function (e) { setError(String(e)); })
      .finally(function () {
        var nb2 = Object.assign({}, busy); delete nb2[key]; setBusy(nb2);
      });
  }

  React.useEffect(function () {
    fetchStatus(); fetchRepos();
    var t = setInterval(fetchStatus, 5000);
    return function () { clearInterval(t); };
  }, []);

  var schedRunning = status && status.scheduler_running;
  var statusBadge = h("span", {
    className: "section-badge",
    style: {
      background: schedRunning ? "rgba(63,185,80,0.15)" : "rgba(248,81,73,0.15)",
      color: schedRunning ? "var(--accent-green)" : "var(--accent-red)",
    },
  }, schedRunning ? "running" : "stopped");

  return h("div", { className: "tab-panel" },
    // Header + global controls
    h("div", { style: { display: "flex", alignItems: "center", gap: 12, marginBottom: 16 } },
      h("h2", { style: { margin: 0 } }, "Cline Agent Launcher"),
      statusBadge,
      status && status.scheduler_pid
        ? h("span", { style: { fontSize: 12, color: "var(--text-secondary)" } },
            "pid " + status.scheduler_pid)
        : null,
    ),
    error
      ? h("div", { className: "alert alert-error", style: { marginBottom: 12 } }, error)
      : null,
    h("div", { style: { display: "flex", gap: 8, marginBottom: 16 } },
      h("button", {
        className: "btn btn-primary",
        disabled: !!busy["start"] || schedRunning,
        onClick: function () { action("start"); },
        "aria-label": "Start scheduler",
      }, busy["start"] ? "starting..." : "Start scheduler"),
      h("button", {
        className: "btn",
        disabled: !!busy["stop"] || !schedRunning,
        onClick: function () { action("stop"); },
        "aria-label": "Stop scheduler",
      }, busy["stop"] ? "stopping..." : "Stop scheduler"),
      h("button", {
        className: "btn",
        disabled: loading,
        onClick: function () { fetchRepos(); fetchStatus(); },
        "aria-label": "Refresh scheduler status",
      }, loading ? "refreshing..." : "Refresh"),
    ),

    // Agents table
    h("h3", null, "Agents"),
    status && status.agents && status.agents.length
      ? h("table", { className: "data-table", style: { width: "100%" } },
          h("thead", null, h("tr", null,
            h("th", null, "Agent"),
            h("th", null, "Enabled"),
            h("th", null, "Interval"),
            h("th", null, "Last run (UTC)"),
            h("th", null, "Last repo"),
            h("th", null, "Window PID"),
            h("th", null, "Lock"),
            h("th", null, "Run now"),
          )),
          h("tbody", null, status.agents.map(function (a) {
            return h("tr", { key: a.name },
              h("td", null, a.name),
              h("td", null, a.enabled ? "yes" : "no"),
              h("td", null, a.interval_seconds + "s"),
              h("td", { style: { fontSize: 12 } }, a.last_run_iso || "-"),
              h("td", null, a.last_repo || "-"),
              h("td", null, a.last_window_pid || "-"),
              h("td", null, a.lock_alive
                ? h("span", { style: { color: "var(--accent-yellow)" } }, "alive")
                : "-"),
              h("td", null,
                h("button", {
                  className: "btn btn-sm",
                  disabled: !!busy["run-once/" + a.name] || a.lock_alive,
                  onClick: function () {
                    action("run-once", { agent: a.name });
                  },
                }, busy["run-once/" + a.name] ? "spawning..." : "Run once"),
              ),
            );
          })),
        )
      : h("div", { style: { color: "var(--text-secondary)" } },
          "No agents configured. Edit %LOCALAPPDATA%\\cline_agent_launcher\\config.json"),

    // Discovered repo inventory
    h("h3", { style: { marginTop: 24 } },
      "Discovered repositories",
      repos ? h("span", {
        className: "section-badge",
        style: { marginLeft: 8, background: "rgba(136,108,228,0.15)", color: "var(--accent-purple)" },
      }, repos.count + " " + (repos.org_filter || "")) : null,
    ),
    repos && repos.repos && repos.repos.length
      ? h("div", { style: { fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 } },
          "WSL distro: " + repos.wsl_distro + ", root: " + repos.repos_root)
      : null,
    repos && repos.repos
      ? h("div", { style: { display: "flex", flexWrap: "wrap", gap: 6 } },
          repos.repos.map(function (r) {
            return h("span", {
              key: r.name,
              className: "section-badge",
              title: r.wsl_path,
              style: { background: "var(--bg-tertiary)" },
            }, r.name);
          }))
      : h("div", { style: { color: "var(--text-secondary)" } },
          loading ? "discovering..." : "No repos discovered yet."),

    // Notes
    h("div", { style: { marginTop: 24, padding: 12, background: "var(--bg-tertiary)", borderRadius: 4, fontSize: 12, color: "var(--text-secondary)" } },
      h("div", null, "Runtime root: " + (status && status.runtime_root || "-")),
      h("div", null, "Status polls every 5s. Repo discovery is on-demand (Refresh button)."),
      h("div", null, "Full config editor (model selector, repo multi-select, intervals) ships in a follow-up — for now edit config.json directly and click Refresh."),
    ),
  );
}

function FeatureRequestsTab(props) {
  var repos = props.repos || [];
  var requests = props.requests || [];
  var templates = props.templates || [];
  var loading = props.loading;
  var promptNotes = props.promptNotes || { notes: "", enabled: true };
  var onDispatch = props.onDispatch;
  var onSaveTemplate = props.onSaveTemplate;
  var onSavePromptNotes = props.onSavePromptNotes;
  var onRefresh = props.onRefresh;
  var frs = React.useState("");
  var selRepo = frs[0],
    setSelRepo = frs[1];
  var fbs = React.useState("main");
  var selBranch = fbs[0],
    setSelBranch = fbs[1];
  var fps = React.useState("jules_api");
  var selProvider = fps[0],
    setSelProvider = fps[1];
  var pts = React.useState("");
  var promptText = pts[0],
    setPromptText = pts[1];
  var sts = React.useState({});
  var selStds = sts[0],
    setSelStds = sts[1];
  var tns = React.useState("");
  var templateName = tns[0],
    setTemplateName = tns[1];
  var dss = React.useState(null);
  var dispatchStatus = dss[0],
    setDispatchStatus = dss[1];
  var svs = React.useState(null);
  var saveStatus = svs[0],
    setSaveStatus = svs[1];
  var pns = React.useState(promptNotes.notes);
  var editingPromptNotes = pns[0],
    setEditingPromptNotes = pns[1];
  var pnse = React.useState(promptNotes.enabled);
  var promptNotesEnabled = pnse[0],
    setPromptNotesEnabled = pnse[1];
  var pnss = React.useState(null);
  var promptNotesSaveStatus = pnss[0],
    setPromptNotesSaveStatus = pnss[1];
  var ALL_STANDARDS = ["tdd", "dbc", "dry", "lod", "security", "docs"];
  function toggleStd(s) {
    setSelStds(function (prev) {
      var next = Object.assign({}, prev);
      if (next[s]) {
        delete next[s];
      } else {
        next[s] = true;
      }
      return next;
    });
  }
  function doDispatch() {
    if (!selRepo || !promptText.trim()) return;
    setDispatchStatus("dispatching");
    var finalPrompt = promptText;
    if (promptNotesEnabled && editingPromptNotes.trim()) {
      finalPrompt = editingPromptNotes + "\\n\\n" + promptText;
    }
    onDispatch({
      repository: selRepo,
      branch: selBranch,
      provider: selProvider,
      prompt: finalPrompt,
      standards: Object.keys(selStds).filter(function (k) {
        return selStds[k];
      }),
    })
      .then(function () {
        setDispatchStatus("ok");
        onRefresh();
      })
      .catch(function () {
        setDispatchStatus("error");
      });
  }
  function doSaveTemplate() {
    if (!templateName.trim() || !promptText.trim()) return;
    setSaveStatus("saving");
    onSaveTemplate({ name: templateName, prompt: promptText })
      .then(function () {
        setSaveStatus("ok");
      })
      .catch(function () {
        setSaveStatus("error");
      });
  }
  function loadTemplate(t) {
    setPromptText(t.prompt);
  }
  function doSavePromptNotes() {
    setPromptNotesSaveStatus("saving");
    onSavePromptNotes({ notes: editingPromptNotes, enabled: promptNotesEnabled })
      .then(function () {
        setPromptNotesSaveStatus("ok");
        setTimeout(function () {
          setPromptNotesSaveStatus(null);
        }, 2000);
      })
      .catch(function () {
        setPromptNotesSaveStatus("error");
      });
  }
  function requestDate(r) {
    return r.created_at || r.dispatched_at
      ? String(r.created_at || r.dispatched_at).slice(0, 10)
      : "";
  }
  function requestStatus(r) {
    return r.status || "dispatched";
  }
  function requestVoteCount(r) {
    return r.votes != null ? r.votes : r.vote_count != null ? r.vote_count : 0;
  }
  return h(
    "div",
    { style: { padding: 20 } },
    h(
      "div",
      { className: "section-header" },
      I.issue(14),
      "Feature Requests",
    ),
    h(
      "div",
      {
        style: {
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: 12,
          marginBottom: 20,
        },
      },
      h(
        "div",
        { style: { marginBottom: 10 } },
        h(
          "div",
          {
            style: {
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 8,
            },
          },
          h("input", {
            type: "checkbox",
            checked: promptNotesEnabled,
            onChange: function (e) {
              setPromptNotesEnabled(e.target.checked);
            },
            style: { cursor: "pointer" },
          }),
          h(
            "label",
            {
              style: {
                fontWeight: 600,
                fontSize: 13,
                cursor: "pointer",
                userSelect: "none",
              },
            },
            "Auto-inject Prompt Notes",
          ),
        ),
        h(
          "div",
          { style: { fontSize: 11, color: "var(--text-muted)", marginBottom: 8 } },
          "These notes will be automatically prepended to every prompt dispatch",
        ),
      ),
      h("textarea", {
        value: editingPromptNotes,
        onChange: function (e) {
          setEditingPromptNotes(e.target.value);
        },
        placeholder:
          "Enter global prompt notes that will be auto-added to every dispatch…",
        rows: 6,
        style: {
          width: "100%",
          background: "var(--bg-primary)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
          borderRadius: 4,
          padding: 8,
          fontSize: 12,
          resize: "vertical",
          boxSizing: "border-box",
          fontFamily: "monospace",
        },
      }),
      h(
        "div",
        { style: { marginTop: 8, display: "flex", gap: 8, alignItems: "center" } },
        h(
          "button",
          {
            className: "action-btn secondary",
            onClick: doSavePromptNotes,
            style: { padding: "4px 12px", fontSize: 12 },
          },
          "Save Notes",
        ),
        promptNotesSaveStatus === "ok"
          ? h(
              "div",
              { style: { color: "var(--accent-green)", fontSize: 11 } },
              "✓ Saved",
            )
          : null,
        promptNotesSaveStatus === "error"
          ? h(
              "div",
              { style: { color: "var(--accent-red)", fontSize: 11 } },
              "✗ Failed",
            )
          : null,
      ),
    ),
    h(
      "div",
      { style: { display: "flex", gap: 20, flexWrap: "wrap" } },
      h(
        "div",
        { style: { flex: "1 1 500px" } },
        h(
          "div",
          {
            style: {
              display: "flex",
              gap: 10,
              marginBottom: 12,
              flexWrap: "wrap",
            },
          },
          h(
            "div",
            {},
            h(
              "label",
              {
                style: {
                  display: "block",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  marginBottom: 4,
                },
              },
              "Repository",
            ),
            h(
              "select",
              {
                value: selRepo,
                onChange: function (e) {
                  setSelRepo(e.target.value);
                },
                style: {
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  borderRadius: 4,
                  padding: "4px 8px",
                  minWidth: 160,
                },
              },
              h("option", { value: "" }, "— pick a repo —"),
              repos.map(function (r) {
                return h(
                  "option",
                  { key: r.name || r, value: r.name || r },
                  r.name || r,
                );
              }),
            ),
          ),
          h(
            "div",
            {},
            h(
              "label",
              {
                style: {
                  display: "block",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  marginBottom: 4,
                },
              },
              "Branch",
            ),
            h("input", {
              value: selBranch,
              onChange: function (e) {
                setSelBranch(e.target.value);
              },
              style: {
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
                borderRadius: 4,
                padding: "4px 8px",
                width: 100,
              },
            }),
          ),
          h(
            "div",
            {},
            h(
              "label",
              {
                style: {
                  display: "block",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  marginBottom: 4,
                },
              },
              "Provider",
            ),
            h(
              "select",
              {
                value: selProvider,
                onChange: function (e) {
                  setSelProvider(e.target.value);
                },
                style: {
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  borderRadius: 4,
                  padding: "4px 8px",
                },
              },
              h("option", { value: "jules_api" }, "Jules"),
              h("option", { value: "codex" }, "Codex"),
              h("option", { value: "claude" }, "Claude"),
            ),
          ),
        ),
        h(
          "div",
          { style: { marginBottom: 10 } },
          h(
            "label",
            {
              style: {
                display: "block",
                fontSize: 11,
                color: "var(--text-muted)",
                marginBottom: 4,
              },
            },
            "Standards to inject",
          ),
          h(
            "div",
            { style: { display: "flex", gap: 6, flexWrap: "wrap" } },
            ALL_STANDARDS.map(function (s) {
              var active = !!selStds[s];
              return h(
                "button",
                {
                  key: s,
                  onClick: function () {
                    toggleStd(s);
                  },
                  style: {
                    padding: "3px 10px",
                    borderRadius: 12,
                    fontSize: 11,
                    cursor: "pointer",
                    border:
                      "1px solid " +
                      (active
                        ? "var(--accent-purple)"
                        : "var(--border)"),
                    background: active
                      ? "rgba(160,130,220,0.15)"
                      : "var(--bg-secondary)",
                    color: active
                      ? "var(--accent-purple)"
                      : "var(--text-muted)",
                  },
                },
                s.toUpperCase(),
              );
            }),
          ),
        ),
        h("textarea", {
          value: promptText,
          onChange: function (e) {
            setPromptText(e.target.value);
          },
          placeholder: "Describe the feature to implement…",
          rows: 8,
          style: {
            width: "100%",
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
            borderRadius: 4,
            padding: 8,
            fontSize: 13,
            resize: "vertical",
            boxSizing: "border-box",
          },
        }),
        h(
          "div",
          {
            style: {
              display: "flex",
              gap: 8,
              marginTop: 10,
              flexWrap: "wrap",
            },
          },
          h(
            "button",
            {
              className: "action-btn",
              disabled: !selRepo || !promptText.trim(),
              onClick: doDispatch,
            },
            I.issue(14),
            " Dispatch",
          ),
          h(
            "div",
            {
              style: { display: "flex", gap: 6, alignItems: "center" },
            },
            h("input", {
              value: templateName,
              onChange: function (e) {
                setTemplateName(e.target.value);
              },
              placeholder: "Template name…",
              style: {
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
                borderRadius: 4,
                padding: "4px 8px",
                fontSize: 12,
                width: 160,
              },
            }),
            h(
              "button",
              {
                className: "action-btn secondary",
                disabled: !templateName.trim() || !promptText.trim(),
                onClick: doSaveTemplate,
              },
              "Save Template",
            ),
          ),
        ),
        dispatchStatus === "ok"
          ? h(
              "div",
              {
                style: {
                  color: "var(--accent-green)",
                  fontSize: 13,
                  marginTop: 8,
                },
              },
              "Feature request dispatched.",
            )
          : null,
        dispatchStatus === "error"
          ? h(
              "div",
              {
                style: {
                  color: "var(--accent-red)",
                  fontSize: 13,
                  marginTop: 8,
                },
              },
              "Dispatch failed.",
            )
          : null,
        saveStatus === "ok"
          ? h(
              "div",
              {
                style: {
                  color: "var(--accent-green)",
                  fontSize: 13,
                  marginTop: 4,
                },
              },
              "Template saved.",
            )
          : null,
        h(
          "div",
          { style: { marginTop: 24 } },
          h(
            "div",
            {
              style: {
                fontWeight: 600,
                fontSize: 13,
                marginBottom: 8,
              },
            },
            "Dispatch History",
          ),
          loading
            ? h(
                "div",
                {
                  style: { color: "var(--text-muted)", fontSize: 12 },
                },
                "Loading…",
              )
            : requests.length === 0
              ? h(
                  "div",
                  {
                    style: {
                      color: "var(--text-muted)",
                      fontSize: 12,
                    },
                  },
                "No dispatched requests yet.",
                )
              : h(
                  "div",
                  {
                    className: "feature-request-desktop-history",
                    style: { maxHeight: 300, overflowY: "auto" },
                  },
                  requests.slice(0, 50).map(function (r, i) {
                    return h(
                      "div",
                      {
                        key: i,
                        style: {
                          borderBottom: "1px solid var(--border)",
                          padding: "8px 0",
                          fontSize: 12,
                        },
                      },
                      h(
                        "div",
                        {
                          style: {
                            display: "flex",
                            justifyContent: "space-between",
                          },
                        },
                        h(
                          "span",
                          {
                            style: {
                              fontWeight: 600,
                              color: "var(--text-primary)",
                            },
                          },
                          r.repository,
                        ),
                        h(
                          "span",
                          { style: { color: "var(--text-muted)" } },
                          requestDate(r),
                        ),
                      ),
                      h(
                        "div",
                        {
                          style: {
                            color: "var(--text-secondary)",
                            marginTop: 2,
                          },
                        },
                        (r.prompt || "").slice(0, 100) +
                          ((r.prompt || "").length > 100
                            ? "…"
                            : ""),
                      ),
                      h(
                        "div",
                        {
                          style: {
                            marginTop: 4,
                            display: "flex",
                            gap: 8,
                          },
                        },
                        h(
                          "span",
                          { style: { color: "var(--text-muted)" } },
                          r.provider || "",
                        ),
                        (r.standards || []).map(function (s) {
                          return h(
                            "span",
                            {
                              key: s,
                              style: {
                                color: "var(--accent-purple)",
                                fontSize: 11,
                              },
                            },
                            s.toUpperCase(),
                          );
                        }),
                      ),
                    );
                  }),
                ),
          requests.length > 0
            ? h(
                "div",
                {
                  className: "feature-request-mobile-list",
                  "aria-label": "Feature request history",
                },
                requests.slice(0, 50).map(function (r, i) {
                  return h(
                    "article",
                    { key: "mobile-feature-" + i, className: "feature-request-mobile-card" },
                    h(
                      "div",
                      { className: "feature-request-mobile-title" },
                      h("span", null, r.repository || "Unknown repository"),
                      h(
                        "span",
                        { className: "feature-request-mobile-chip feature-request-mobile-status" },
                        requestStatus(r),
                      ),
                    ),
                    h(
                      "div",
                      { className: "feature-request-mobile-prompt" },
                      (r.prompt || "").slice(0, 180) +
                        ((r.prompt || "").length > 180 ? "\u2026" : ""),
                    ),
                    h(
                      "div",
                      { className: "feature-request-mobile-meta" },
                      h(
                        "span",
                        { className: "feature-request-mobile-chip" },
                        requestVoteCount(r) + " votes",
                      ),
                      h(
                        "span",
                        { className: "feature-request-mobile-chip" },
                        r.provider || "provider unknown",
                      ),
                      h(
                        "span",
                        { className: "feature-request-mobile-chip" },
                        requestDate(r) || "date unknown",
                      ),
                    ),
                  );
                }),
              )
            : null,
        ),
      ),
      h(
        "div",
        { style: { flex: "0 1 240px" } },
        h(
          "div",
          {
            style: {
              fontWeight: 600,
              fontSize: 13,
              marginBottom: 8,
            },
          },
          "Saved Templates",
        ),
        templates.length === 0
          ? h(
              "div",
              {
                style: { color: "var(--text-muted)", fontSize: 12 },
              },
              "No saved templates.",
            )
          : h(
              "div",
              {
                style: {
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                },
              },
              templates.map(function (t, i) {
                return h(
                  "div",
                  {
                    key: i,
                    style: {
                      background: "var(--bg-secondary)",
                      border: "1px solid var(--border)",
                      borderRadius: 4,
                      padding: "8px 10px",
                      cursor: "pointer",
                    },
                    onClick: function () {
                      loadTemplate(t);
                    },
                  },
                  h(
                    "div",
                    {
                      style: {
                        fontWeight: 600,
                        fontSize: 12,
                        color: "var(--text-primary)",
                      },
                    },
                    t.name,
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
                    (t.prompt || "").slice(0, 60) + "…",
                  ),
                );
              }),
            ),
      ),
    ),
  );
}


function DiagnosticsTab() {
  var ds = React.useState(null);
  var data = ds[0], setData = ds[1];
  var ls = React.useState(true);
  var loading = ls[0], setLoading = ls[1];
  var es = React.useState(null);
  var error = es[0], setError = es[1];
  var drs = React.useState(null);
  var driftData = drs[0], setDriftData = drs[1];
  var rs2 = React.useState(null);
  var restartResult = rs2[0], setRestartResult = rs2[1];
  var rcs = React.useState(false);
  var restartConfirm = rcs[0], setRestartConfirm = rcs[1];
  var rls = React.useState(false);
  var restartLoading = rls[0], setRestartLoading = rls[1];
  var lrs = React.useState(null);
  var launcherResult = lrs[0], setLauncherResult = lrs[1];
  var lls = React.useState(false);
  var launcherLoading = lls[0], setLauncherLoading = lls[1];

  function fetchDiagnostics() {
    setLoading(true);
    Promise.all([
      fetch("/api/diagnostics/summary").then(function (r) { return r.json(); }),
      fetch("/api/deployment/git-drift").then(function (r) { return r.json(); }),
    ])
      .then(function (results) {
        setData(results[0] || {});
        setDriftData(results[1] || {});
        setError(null);
        setLoading(false);
      })
      .catch(function (e) {
        setError(e.message || "Failed to load diagnostics");
        setLoading(false);
      });
  }

  React.useEffect(function () { fetchDiagnostics(); }, []);

  function doRestartService() {
    setRestartLoading(true);
    setRestartResult(null);
    fetch("/api/diagnostics/restart-service", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        setRestartResult(d);
        setRestartConfirm(false);
        setRestartLoading(false);
      })
      .catch(function (e) {
        setRestartResult({ success: false, output: e.message || "Request failed" });
        setRestartConfirm(false);
        setRestartLoading(false);
      });
  }

  function doGenerateLaunchers() {
    setLauncherLoading(true);
    setLauncherResult(null);
    fetch("/api/launchers/generate", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) { setLauncherResult(d); setLauncherLoading(false); })
      .catch(function (e) {
        setLauncherResult({ message: "Error: " + (e.message || "Request failed") });
        setLauncherLoading(false);
      });
  }

  var cardStyle = {
    background: "var(--card-bg)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    padding: "16px 20px",
    marginBottom: 16,
  };
  var sectionHeadStyle = {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    marginBottom: 12,
  };
  var kvStyle = { display: "flex", gap: 8, marginBottom: 6, fontSize: 13 };
  var keyStyle = { color: "var(--text-muted)", minWidth: 140 };
  var valStyle = { color: "var(--text-primary)", fontFamily: "monospace" };
  var btnStyle = {
    background: "var(--accent-blue)",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    padding: "6px 14px",
    cursor: "pointer",
    fontSize: 13,
    marginRight: 8,
  };
  var dangerBtnStyle = Object.assign({}, btnStyle, { background: "var(--accent-red)" });
  var warnBannerStyle = {
    background: "rgba(210,153,34,0.15)",
    border: "1px solid var(--accent-yellow)",
    borderRadius: 6,
    padding: "10px 14px",
    marginBottom: 16,
    fontSize: 13,
    color: "var(--accent-yellow)",
  };

  if (loading) return h("div", { style: { padding: 24 } }, "Loading diagnostics…");
  if (error) return h("div", { style: { padding: 24, color: "var(--accent-red)" } }, "Error: ", error);

  var isDrifted = (driftData && driftData.is_drifted) || (data && data.is_drifted);

  return h("div", { style: { padding: 20, maxWidth: 860 } },
    h("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 } },
      h("h2", { style: { margin: 0, fontSize: 20, fontWeight: 700 } }, "Diagnostics"),
      h("button", { style: btnStyle, onClick: fetchDiagnostics, "aria-label": "Refresh diagnostics" }, I.refresh(12), " Refresh"),
    ),

    isDrifted
      ? h("div", { style: warnBannerStyle },
          "⚠️ Deployed version is behind origin/main. Run update-deployed.sh to update.",
          h("div", { style: { marginTop: 6, fontSize: 12, opacity: 0.85 } },
            "Local: ", (driftData && driftData.source_commit) || (data && data.source_commit) || "?",
            " → Remote: ", (driftData && driftData.remote_commit) || (data && data.remote_commit) || "?",
          ),
        )
      : null,

    h("div", { style: cardStyle },
      h("div", { style: sectionHeadStyle }, "System Overview"),
      data
        ? h("div", null,
            h("div", { style: kvStyle }, h("span", { style: keyStyle }, "Dashboard PID"), h("span", { style: valStyle }, data.dashboard_pid || "—")),
            h("div", { style: kvStyle }, h("span", { style: keyStyle }, "Memory"), h("span", { style: valStyle }, (data.dashboard_memory_mb || 0) + " MB")),
            h("div", { style: kvStyle }, h("span", { style: keyStyle }, "Port"), h("span", { style: valStyle }, data.dashboard_port || 8321)),
            h("div", { style: kvStyle }, h("span", { style: keyStyle }, "Git Commit"), h("span", { style: valStyle }, data.git_commit || "unknown")),
            h("div", { style: kvStyle }, h("span", { style: keyStyle }, "Drift Status"), h("span", { style: valStyle }, isDrifted ? "Behind origin/main" : "Up to date")),
          )
        : h("div", null, "No data"),
    ),

    h("div", { style: cardStyle },
      h("div", { style: sectionHeadStyle }, "WSL Status"),
      data
        ? h("div", null,
            h("div", { style: { fontSize: 12, marginBottom: 8, color: (data.wsl_available ? "var(--accent-green)" : "var(--accent-red)") } },
              data.wsl_available ? "WSL available" : "WSL not available"),
            data.wsl_status
              ? h("pre", { style: { background: "var(--bg-secondary)", borderRadius: 4, padding: 10, fontSize: 11, margin: 0, overflowX: "auto", color: "var(--text-primary)", whiteSpace: "pre-wrap" } },
                  data.wsl_status)
              : null,
          )
        : h("div", null, "No WSL data"),
    ),

    h("div", { style: cardStyle },
      h("div", { style: sectionHeadStyle }, "Recovery Actions"),
      h("div", { style: { marginBottom: 12 } },
        restartConfirm
          ? h("div", null,
              h("span", { style: { fontSize: 13, marginRight: 8, color: "var(--accent-yellow)" } }, "Restart the runner-dashboard systemd service?"),
              h("button", {
                style: dangerBtnStyle,
                onClick: doRestartService,
                disabled: restartLoading,
              }, restartLoading ? "Restarting…" : "Confirm Restart"),
              h("button", { style: Object.assign({}, btnStyle, { background: "var(--card-bg)", color: "var(--text-primary)", border: "1px solid var(--border)" }), onClick: function () { setRestartConfirm(false); }, "aria-label": "Cancel restart" }, "Cancel"),
            )
          : h("button", {
              style: dangerBtnStyle,
              onClick: function () { setRestartConfirm(true); setRestartResult(null); },
            }, "Restart Dashboard Service"),
        restartResult
          ? h("div", {
              style: {
                marginTop: 8,
                padding: "8px 12px",
                background: restartResult.success ? "rgba(63,185,80,0.1)" : "rgba(248,81,73,0.1)",
                borderRadius: 4,
                fontSize: 12,
                color: restartResult.success ? "var(--accent-green)" : "var(--accent-red)",
              }
            },
              restartResult.success ? "Service restarted successfully." : "Restart failed.",
              restartResult.output ? h("pre", { style: { margin: "4px 0 0", fontSize: 11, whiteSpace: "pre-wrap" } }, restartResult.output) : null,
            )
          : null,
      ),
    ),

    h("div", { style: cardStyle },
      h("div", { style: sectionHeadStyle }, "Windows Launchers"),
      h("p", { style: { fontSize: 13, color: "var(--text-muted)", marginBottom: 12 } },
        "Generate PowerShell scripts on your Windows Desktop for quick access."),
      h("button", {
        style: btnStyle,
        onClick: doGenerateLaunchers,
        disabled: launcherLoading,
      }, launcherLoading ? "Generating…" : "Generate Launchers"),
      launcherResult
        ? h("div", { style: { marginTop: 12, fontSize: 12 } },
            h("div", { style: { color: "var(--accent-green)", marginBottom: 6 } }, launcherResult.message || "Done"),
            launcherResult.output_dir
              ? h("div", { style: { color: "var(--text-muted)", marginBottom: 6 } }, "Output: ", h("code", null, launcherResult.output_dir))
              : null,
            launcherResult.launchers && launcherResult.launchers.length > 0
              ? h("ul", { style: { margin: "4px 0", paddingLeft: 20 } },
                  launcherResult.launchers.map(function (f) {
                    return h("li", { key: f, style: { fontFamily: "monospace", fontSize: 11 } }, f.split(/[\\/]/).pop());
                  }),
                )
              : null,
          )
        : null,
    ),

    h("div", { style: cardStyle },
      h("div", { style: sectionHeadStyle }, "Quick API Links"),
      h("div", { style: { display: "flex", flexWrap: "wrap", gap: 8 } },
        ["/api/health", "/api/system", "/api/runners", "/api/diagnostics/summary", "/api/deployment/drift"].map(function (url) {
          return h("a", { key: url, href: url, target: "_blank", rel: "noopener noreferrer", style: { fontSize: 12, color: "var(--accent-blue)", textDecoration: "none", padding: "4px 8px", background: "var(--bg-secondary)", borderRadius: 4 } }, url);
        }),
      ),
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
      try {
        localStorage.removeItem(ASST_LS.transcript);
        localStorage.removeItem(ASST_LS.transcriptTimestamp);
      } catch (e) {}
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

    fetch("/api/assistant/chat", {
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
                try {
                  localStorage.removeItem(ASST_LS.transcript);
                  localStorage.removeItem(ASST_LS.transcriptTimestamp);
                } catch (ex) {}
              }
            },
            style: { accentColor: "var(--accent-blue)" },
          }),
          "Save chat history"
        ),
        h("button", {
          onClick: function () {
            setTranscript([]);
            try {
              localStorage.removeItem(ASST_LS.transcript);
              localStorage.removeItem(ASST_LS.transcriptTimestamp);
            } catch (e) {}
            setShowSettings(false);
          },
          style: { background: "var(--accent-red)", color: "#fff", border: "none", borderRadius: 6, padding: "6px 12px", cursor: "pointer", fontSize: 12, width: "100%", marginTop: 8 },
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
                  color: isUser ? "#fff" : "var(--text-primary)",
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
            h("button", {
              onClick: sendMessage,
              disabled: loading || !inputVal.trim(),
              style: {
                background: "var(--accent-blue)",
                color: "#fff",
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
      )
    : null;

  return h("div", { style: sidebarStyle, "aria-label": "Assistant sidebar" },
    open ? h("div", { style: dragHandleStyle, onMouseDown: startDrag }) : null,
    open ? h(React.Fragment, null,
      h("div", { style: headerStyle },
        h("span", { style: { fontWeight: 600, fontSize: 13 } }, "✨ Assistant"),
        h("div", { style: { display: "flex", gap: 6 } },
          h("button", {
            onClick: function () { setShowSettings(function (s) { return !s; }); },
            title: "Settings",
            style: { background: "none", border: "none", color: showSettings ? "var(--accent-blue)" : "var(--text-muted)", cursor: "pointer", fontSize: 15, lineHeight: 1 },
          }, "⚙️"),
          h("button", {
            onClick: toggle,
            title: "Close",
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

  // Close on outside click
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
    document.addEventListener("mousedown", onMouseDown);
    return function () { document.removeEventListener("mousedown", onMouseDown); };
  }, [open]);

  // Fetch repos and providers when popover opens
  React.useEffect(function () {
    if (!open) return;
    if (repoList.length === 0) {
      fetch("/api/repos")
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
      fetch("/api/agents/providers")
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
    fetch("/api/agents/quick-dispatch", {
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
            style: {
              position: "fixed",
              right: 16,
              top: 64,
              width: 320,
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

function PrincipalsTab() {
  var ps = React.useState([]);
  var principals = ps[0], setPrincipals = ps[1];

  var ts = React.useState([]);
  var tokens = ts[0], setTokens = ts[1];

  var errs = React.useState(null);
  var error = errs[0], setError = errs[1];

  var edQuotaState = React.useState(null);
  var editingQuota = edQuotaState[0], setEditingQuota = edQuotaState[1];

  var mintTokenState = React.useState(null);
  var mintingToken = mintTokenState[0], setMintingToken = mintTokenState[1];
  var createdTokenState = React.useState(null);
  var createdToken = createdTokenState[0], setCreatedToken = createdTokenState[1];

  function loadData() {
    fetch("/api/admin/principals")
.then(function (r) {
  if (!r.ok) throw new Error("Failed to load principals");
  return r.json();
})
.then(function (data) { setPrincipals(data.principals); })
.catch(function (e) { setError(e.message); });

    fetch("/api/admin/tokens")
.then(function (r) {
  if (!r.ok) throw new Error("Failed to load tokens");
  return r.json();
})
.then(function (data) { setTokens(data.tokens); })
.catch(function (e) { setError(e.message); });
  }

  React.useEffect(function () {
    loadData();
  }, []);

  function saveQuota(e) {
    e.preventDefault();
    var q = editingQuota.quotas;
    fetch("/api/admin/principals/" + editingQuota.principalId + "/quota", {
method: "PATCH",
headers: { "Content-Type": "application/json" },
body: JSON.stringify({
  max_runners: parseInt(q.max_runners),
  agent_spend_usd_day: parseFloat(q.agent_spend_usd_day),
  local_app_slots: parseInt(q.local_app_slots)
})
    })
    .then(function(r) {
if(!r.ok) throw new Error("Failed to update quota");
setEditingQuota(null);
loadData();
    })
    .catch(function(err) { setError(err.message); });
  }

  function doMintToken(e) {
    e.preventDefault();
    var name = e.target.elements.name.value;
    var exp = e.target.elements.expires.value;
    var body = { name: name };
    if (exp) body.expires_in_days = parseInt(exp);

    fetch("/api/admin/principals/" + mintingToken + "/token", {
method: "POST",
headers: { "Content-Type": "application/json" },
body: JSON.stringify(body)
    })
    .then(function(r) {
if (!r.ok) throw new Error("Failed to mint token");
return r.json();
    })
    .then(function(data) {
setMintingToken(null);
setCreatedToken(data.token);
loadData();
    })
    .catch(function(err) { setError(err.message); });
  }

  function revokeToken(hash) {
    if(!confirm("Are you sure you want to revoke this token?")) return;
    fetch("/api/admin/tokens/" + hash, { method: "DELETE" })
.then(function(r) {
  if(!r.ok) throw new Error("Failed to revoke token");
  loadData();
})
.catch(function(err) { setError(err.message); });
  }

  return h("div", { className: "section", style: { marginTop: "16px" } },
    error && h("div", { className: "error-banner", style: { marginBottom: "16px", padding: "12px", background: "rgba(255,0,0,0.1)", borderLeft: "4px solid red", color: "var(--text-primary)" } }, error),

    createdToken && h("div", { className: "glass-card", style: { padding: "20px", marginBottom: "20px", border: "1px solid var(--accent-green)", background: "rgba(46, 160, 67, 0.1)" } },
h("h3", { style: { color: "var(--accent-green)", marginBottom: "8px" } }, "Token Successfully Created!"),
h("p", { style: { marginBottom: "12px" } }, "Please copy this token now. You will not be able to see it again."),
h("div", { style: { background: "var(--bg-secondary)", padding: "12px", borderRadius: "6px", fontFamily: "monospace", fontSize: "16px", wordBreak: "break-all", border: "1px solid var(--border)" } }, createdToken),
h("button", { className: "btn", style: { marginTop: "12px" }, onClick: function() { setCreatedToken(null); }, "aria-label": "Dismiss token" }, "Dismiss")
    ),

    editingQuota && h("div", {
  style: { position: "fixed", top: 0, left: 0, right: 0, bottom: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }
},
h("div", { className: "glass-card", style: { padding: "24px", width: "400px", maxWidth: "90%" } },
  h("h3", { style: { marginBottom: "16px" } }, "Edit Quota: " + editingQuota.principalId),
  h("form", { onSubmit: saveQuota, style: { display: "flex", flexDirection: "column", gap: "12px" } },
    h("div", null,
      h("label", { style: { display: "block", marginBottom: "4px", fontSize: "12px", color: "var(--text-secondary)" } }, "Max Runners"),
      h("input", {
        type: "number",
        value: editingQuota.quotas.max_runners,
        onChange: function(e) { setEditingQuota(Object.assign({}, editingQuota, { quotas: Object.assign({}, editingQuota.quotas, { max_runners: e.target.value })})); },
        style: { width: "100%", padding: "8px", background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "white", borderRadius: "4px" }
      })
    ),
    h("div", null,
      h("label", { style: { display: "block", marginBottom: "4px", fontSize: "12px", color: "var(--text-secondary)" } }, "Agent Spend (USD/Day)"),
      h("input", {
        type: "number", step: "0.01",
        value: editingQuota.quotas.agent_spend_usd_day,
        onChange: function(e) { setEditingQuota(Object.assign({}, editingQuota, { quotas: Object.assign({}, editingQuota.quotas, { agent_spend_usd_day: e.target.value })})); },
        style: { width: "100%", padding: "8px", background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "white", borderRadius: "4px" }
      })
    ),
    h("div", null,
      h("label", { style: { display: "block", marginBottom: "4px", fontSize: "12px", color: "var(--text-secondary)" } }, "Local App Slots"),
      h("input", {
        type: "number",
        value: editingQuota.quotas.local_app_slots,
        onChange: function(e) { setEditingQuota(Object.assign({}, editingQuota, { quotas: Object.assign({}, editingQuota.quotas, { local_app_slots: e.target.value })})); },
        style: { width: "100%", padding: "8px", background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "white", borderRadius: "4px" }
      })
    ),
    h("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end", marginTop: "16px" } },
      h("button", { type: "button", className: "btn", onClick: function() { setEditingQuota(null); }, "aria-label": "Cancel editing quota" }, "Cancel"),
      h("button", { type: "submit", className: "btn", style: { background: "var(--accent-blue)", color: "white", borderColor: "var(--accent-blue)" }, "aria-label": "Save quota settings" }, "Save")
    )
  )
)
    ),

    mintingToken && h("div", {
  style: { position: "fixed", top: 0, left: 0, right: 0, bottom: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }
},
h("div", { className: "glass-card", style: { padding: "24px", width: "400px", maxWidth: "90%" } },
  h("h3", { style: { marginBottom: "16px" } }, "Mint Token: " + mintingToken),
  h("form", { onSubmit: doMintToken, style: { display: "flex", flexDirection: "column", gap: "12px" } },
    h("div", null,
      h("label", { style: { display: "block", marginBottom: "4px", fontSize: "12px", color: "var(--text-secondary)" } }, "Token Name"),
      h("input", {
        name: "name",
        type: "text",
        placeholder: "e.g., prod-deployment-script",
        required: true,
        style: { width: "100%", padding: "8px", background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "white", borderRadius: "4px" }
      })
    ),
    h("div", null,
      h("label", { style: { display: "block", marginBottom: "4px", fontSize: "12px", color: "var(--text-secondary)" } }, "Expires in Days (Optional)"),
      h("input", {
        name: "expires",
        type: "number",
        placeholder: "e.g., 30",
        style: { width: "100%", padding: "8px", background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "white", borderRadius: "4px" }
      })
    ),
    h("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end", marginTop: "16px" } },
      h("button", { type: "button", className: "btn", onClick: function() { setMintingToken(null); }, "aria-label": "Cancel minting token" }, "Cancel"),
      h("button", { type: "submit", className: "btn", style: { background: "var(--accent-green)", color: "white", borderColor: "var(--accent-green)" }, "aria-label": "Mint access token" }, "Mint")
    )
  )
)
    ),

    h("div", { className: "section-header", style: { background: "var(--grad-fair)", color: "white" } },
h("div", { className: "section-title" }, I.server(16), "Registered Principals"),
h("span", { className: "section-badge", style: { background: "rgba(255,255,255,0.2)", color: "white" } }, principals.length)
    ),
    h("div", { className: "section-body", style: { padding: "0" } },
h("table", { className: "data-table", style: { width: "100%", borderCollapse: "collapse" } },
  h("thead", null,
    h("tr", { style: { borderBottom: "1px solid var(--border)", textAlign: "left", background: "var(--bg-secondary)" } },
      h("th", { style: { padding: "12px" } }, "ID"),
      h("th", { style: { padding: "12px" } }, "Type"),
      h("th", { style: { padding: "12px" } }, "Roles"),
      h("th", { style: { padding: "12px" } }, "Quotas"),
      h("th", { style: { padding: "12px", textAlign: "right" } }, "Actions")
    )
  ),
  h("tbody", null,
    principals.map(function(p) {
      return h("tr", { key: p.id, style: { borderBottom: "1px solid var(--border)" } },
        h("td", { style: { padding: "12px", fontWeight: "600", color: "var(--accent-blue)" } }, p.id),
        h("td", { style: { padding: "12px" } },
          h("span", { className: "section-badge", style: { background: p.type === 'bot' ? "rgba(188, 140, 255, 0.15)" : "rgba(88, 166, 255, 0.15)", color: p.type === 'bot' ? "var(--accent-purple)" : "var(--accent-blue)" } }, p.type)
        ),
        h("td", { style: { padding: "12px" } },
          p.roles.map(function(r) { return h("span", { key: r, className: "section-badge", style: { marginRight: "4px", background: "rgba(255,255,255,0.1)", color: "var(--text-secondary)" } }, r); })
        ),
        h("td", { style: { padding: "12px", fontSize: "12px", color: "var(--text-secondary)" } },
          h("div", null, "Runners: ", h("strong", { style: { color: "var(--text-primary)" } }, p.quotas.max_runners)),
          h("div", null, "Spend: $", h("strong", { style: { color: "var(--text-primary)" } }, parseFloat(p.quotas.agent_spend_usd_day).toFixed(2)), "/day"),
          h("div", null, "App Slots: ", h("strong", { style: { color: "var(--text-primary)" } }, p.quotas.local_app_slots))
        ),
        h("td", { style: { padding: "12px", textAlign: "right" } },
          h("button", {
            className: "btn",
            style: { marginRight: "8px", fontSize: "12px", padding: "4px 8px" },
            onClick: function() { setEditingQuota({ principalId: p.id, quotas: Object.assign({}, p.quotas) }); }
          }, "Edit Quota"),
          p.type === 'bot' && h("button", {
            className: "btn",
            style: { fontSize: "12px", padding: "4px 8px", background: "rgba(188, 140, 255, 0.1)", color: "var(--accent-purple)", borderColor: "var(--accent-purple)" },
            onClick: function() { setMintingToken(p.id); }
          }, "Mint Token")
        )
      );
    })
  )
)
    ),

    h("div", { className: "section-header", style: { background: "var(--glass-bg)", borderTop: "1px solid var(--border)", color: "var(--text-primary)", marginTop: "24px" } },
h("div", { className: "section-title" }, I.server(16), "Active Service Tokens"),
h("span", { className: "section-badge", style: { background: "rgba(255,255,255,0.1)", color: "var(--text-secondary)" } }, tokens.length)
    ),
    h("div", { className: "section-body", style: { padding: "0" } },
h("table", { className: "data-table", style: { width: "100%", borderCollapse: "collapse" } },
  h("thead", null,
    h("tr", { style: { borderBottom: "1px solid var(--border)", textAlign: "left", background: "var(--bg-secondary)" } },
      h("th", { style: { padding: "12px" } }, "Principal"),
      h("th", { style: { padding: "12px" } }, "Name"),
      h("th", { style: { padding: "12px" } }, "Token Hash"),
      h("th", { style: { padding: "12px" } }, "Created"),
      h("th", { style: { padding: "12px", textAlign: "right" } }, "Actions")
    )
  ),
  h("tbody", null,
    tokens.length === 0 && h("tr", null, h("td", { colSpan: 5, style: { padding: "24px", textAlign: "center", color: "var(--text-secondary)" } }, "No active service tokens found.")),
    tokens.map(function(t) {
      return h("tr", { key: t.hash, style: { borderBottom: "1px solid var(--border)" } },
        h("td", { style: { padding: "12px", fontWeight: "600" } }, t.principal_id),
        h("td", { style: { padding: "12px", color: "var(--text-secondary)" } }, t.name || "-"),
        h("td", { style: { padding: "12px", fontFamily: "monospace", fontSize: "12px", color: "var(--text-secondary)" } }, t.hash.substring(0, 16) + "..."),
        h("td", { style: { padding: "12px", fontSize: "12px", color: "var(--text-secondary)" } }, new Date(t.created_at).toLocaleString()),
        h("td", { style: { padding: "12px", textAlign: "right" } },
          h("button", {
            className: "btn btn-red",
            style: { fontSize: "12px", padding: "4px 8px" },
            onClick: function() { revokeToken(t.hash); }
          }, "Revoke")
        )
      );
    })
  )
)
    )
  );
}

function App({ initialTab }: { initialTab?: string } = {}) {
  var ts = React.useState(initialTab ?? "overview");
  var tab = ts[0],
    setTab = ts[1];
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
  var sjs = React.useState({});
  var scheduledJobs = sjs[0],
    setScheduledJobs = sjs[1];
  var sjl = React.useState(false);
  var scheduledJobsLoading = sjl[0],
    setScheduledJobsLoading = sjl[1];
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
  var rauS = React.useState({ violations: [], last_checked: null, error: null });
  var runnerAudit = rauS[0], setRunnerAudit = rauS[1];
  var rauDismissS = React.useState(false);
  var auditBannerDismissed = rauDismissS[0], setAuditBannerDismissed = rauDismissS[1];

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
    fetch("/api/fleet/orchestration")
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
    return fetch("/api/fleet/orchestration/dispatch", {
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
    return fetch("/api/fleet/orchestration/deploy", {
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
    fetch("/api/credentials")
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

  function setCredentialKey(probe) {
    var provider = probe && probe.key_provider;
    if (!provider) return;
    var providerLabel = probe.label || probe.name || provider;
    var keyValue = window.prompt("Enter API key for " + providerLabel);
    if (!keyValue) return;
    fetch("/api/credentials/set-key", {
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
    fetch("/api/workflows/list")
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
    return fetch("/api/workflows/dispatch", {
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
    fetch("/api/maxwell/status")
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
    return fetch("/api/maxwell/control", {
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
    fetch("/api/stats")
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
      fetch("/api/runners")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      fetch("/api/runs?per_page=30")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      fetch("/api/system")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      fetch("/api/fleet/schedule")
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
          setConnected(true);
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
    fetch("/api/repos")
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
    fetch("/api/heavy-tests/repos")
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
    fetch("/api/tests/ci-results")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.results) setCiResults(d.results);
      })
      .catch(function () {});
  }
  function fetchReports() {
    setReportsLoading(true);
    fetch("/api/reports")
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
    fetch("/api/queue")
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
    fetch("/api/fleet/nodes")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setMachinesData(d);
        setMachinesLoading(false);
      })
      .catch(function () {
        setMachinesLoading(false);
      });
  }
  function fetchLocalApps() {
    setLocalAppsLoading(true);
    fetch("/api/local-apps")
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
    fetch("/api/runs/enriched?per_page=50")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.workflow_runs) setEnrichedRuns(d.workflow_runs);
      })
      .catch(function () {});
  }
  function fetchWatchdog() {
    fetch("/api/watchdog")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setWatchdog(d);
      })
      .catch(function () {});
  }
  function fetchDeployment() {
    fetch("/api/deployment")
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
    fetch("/api/deployment/state")
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
      fetch("/api/agent-remediation/config").then(function (r) {
        return r.json();
      }),
      fetch("/api/agent-remediation/workflows").then(function (r) {
        return r.json();
      }),
      fetch("/api/agent-remediation/history")
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
    return fetch("/api/agent-remediation/config", {
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
    fetch("/api/agent-remediation/plan", {
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
    fetch("/api/agent-remediation/dispatch", {
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
        fetch("/api/agent-remediation/history")
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
    fetch("/api/scheduled-workflows")
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
    fetch("/api/fleet/schedule")
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
    fetch("/api/fleet/schedule", {
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
    fetch("/api/assessments/scores")
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
    return fetch("/api/assessments/dispatch", {
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
      fetch("/api/feature-requests")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return { requests: [] };
        }),
      fetch("/api/feature-requests/templates")
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
    return fetch("/api/feature-requests/dispatch", {
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
    return fetch("/api/prompt-templates", {
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
    return fetch("/api/settings/prompt-notes", {
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
    fetch("/api/stats")
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
      fetch("/api/runners")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      fetch("/api/runs?per_page=30")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      fetch("/api/system")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      fetch("/api/fleet/schedule")
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
          setConnected(true);
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
    fetch("/api/repos")
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
    fetch("/api/heavy-tests/repos")
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
    fetch("/api/tests/ci-results")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.results) setCiResults(d.results);
      })
      .catch(function () {});
  }
  function fetchReports() {
    setReportsLoading(true);
    fetch("/api/reports")
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
    fetch("/api/queue")
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
    fetch("/api/fleet/nodes")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setMachinesData(d);
        setMachinesLoading(false);
      })
      .catch(function () {
        setMachinesLoading(false);
      });
  }
  function fetchLocalApps() {
    setLocalAppsLoading(true);
    fetch("/api/local-apps")
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
    fetch("/api/runs/enriched?per_page=50")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.workflow_runs) setEnrichedRuns(d.workflow_runs);
      })
      .catch(function () {});
  }
  function fetchWatchdog() {
    fetch("/api/watchdog")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setWatchdog(d);
      })
      .catch(function () {});
  }
  function fetchDeployment() {
    fetch("/api/deployment")
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
    fetch("/api/deployment/state")
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
      fetch("/api/agent-remediation/config").then(function (r) {
        return r.json();
      }),
      fetch("/api/agent-remediation/workflows").then(function (r) {
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
    return fetch("/api/agent-remediation/config", {
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
    fetch("/api/agent-remediation/plan", {
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
    fetch("/api/agent-remediation/dispatch", {
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
    fetch("/api/scheduled-workflows")
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
    fetch("/api/fleet/schedule")
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
    fetch("/api/fleet/schedule", {
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
    fetch("/api/auth/me")
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) { setPrincipal(d); })
      .catch(function() { setPrincipal(null); });
  }

  function fetchRunnerAudit() {
    fetch("/api/runner-routing-audit")
      .then(function(r) { return r.json(); })
      .then(function(d) {
        setRunnerAudit(d || { violations: [], last_checked: null, error: null });
        setAuditBannerDismissed(false);
      })
      .catch(function() {});
  }

  function triggerRunnerAuditRefresh() {
    fetch("/api/runner-routing-audit/refresh", {
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
      fetch("/health", { method: "GET" })
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
    fetch("/api/fleet/control/" + a, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
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
    fetch("/api/runners/" + id + "/" + a, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
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

  return h(
    "div",
    null,
    h(
      "header",
      { className: "app-header" },
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
              h("stop", { offset: "0%", stopColor: "#4f8ff7" }),
              h("stop", { offset: "100%", stopColor: "#a855f7" }),
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
            className: "tab-btn" + (tab === "stats" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "stats",
            onClick: function () {
              setTab("stats");
            },
          },
          I.activity(14),
          "Stats",
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "reports" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "reports",
            onClick: function () {
              setTab("reports");
            },
          },
          I.fileText(14),
          "Reports",
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
                    color: "#58a6ff",
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
            className: "tab-btn" + (tab === "history" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "history",
            onClick: function () {
              setTab("history");
              fetchEnrichedRuns();
            },
          },
          I.activity(14),
          "History",
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
      ),
      h(
        "div",
        { className: "header-right" },
        (function() {
          // Mock quota data for visualization
          var quotaUsed = 14;
          var quotaTotal = 20;
          var percent = (quotaUsed / quotaTotal) * 100;
          return h(
            "div",
            {
              className: "glass-card",
              style: {
                padding: "4px 12px",
                display: "flex",
                alignItems: "center",
                gap: "10px",
                marginRight: "12px",
                fontSize: "11px",
                height: "32px"
              }
            },
            h("span", { style: { color: "var(--text-secondary)", fontWeight: "600" } }, "FLEET QUOTA"),
            h(
              "div",
              {
                className: "progress-bar",
                style: { width: "80px", height: "6px", background: "rgba(255,255,255,0.1)" },
                title: quotaUsed + "/" + quotaTotal + " Runners Active"
              },
              h("div", {
                className: "progress-fill",
                style: {
                  width: percent + "%",
                  background: "var(--grad-quota)",
                  boxShadow: "0 0 10px rgba(0, 242, 254, 0.5)"
                }
              })
            ),
            h("span", { style: { color: "var(--text-primary)", fontWeight: "700" } }, quotaUsed + "/" + quotaTotal)
          );
        })(),
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
              fetch("/api/auth/logout", {method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" }})
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
            title: deployment.deployed_at || "Deployment revision",
          },
          "Build " + shortSha(deployment.git_sha),
        ),
        h("button", {
          className: "btn",
          style: { marginLeft: 4, background: asstOpen ? "var(--accent-blue)" : undefined, color: asstOpen ? "#fff" : undefined },
          onClick: toggleAsst,
          title: "Toggle Assistant sidebar",
        }, "☰ Asst"),
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
    ),
    (runnerAudit.violations && runnerAudit.violations.length > 0 && !auditBannerDismissed)
      ? h(
          "div",
          {
            id: "hosted-runner-alert-banner",
            style: {
              background: "linear-gradient(90deg, rgba(248,81,73,0.18) 0%, rgba(240,136,62,0.18) 100%)",
              borderBottom: "2px solid var(--accent-red)",
              padding: "10px 24px",
              display: "flex",
              alignItems: "center",
              gap: "12px",
              fontSize: "13px",
              fontWeight: "500",
              color: "var(--text-primary)",
              zIndex: 90,
              position: "sticky",
              top: "56px",
            },
          },
          h("span", { style: { fontSize: "18px" } }, "⚠️"),
          h(
            "span",
            { style: { flex: 1 } },
            h("strong", { style: { color: "var(--accent-red)" } }, "BILLING ALERT: "),
            runnerAudit.violations.length + " workflow job(s) detected on GitHub-hosted runners. This incurs unexpected billing costs.",
          ),
          h(
            "button",
            {
              className: "btn",
              style: { background: "rgba(248,81,73,0.2)", color: "var(--accent-red)", border: "1px solid var(--accent-red)", fontSize: "12px" },
              onClick: function() { setTab("runner-audit"); },
            },
            "View Details",
          ),
          h(
            "button",
            {
              className: "btn",
              style: { fontSize: "12px", marginLeft: 4 },
              title: "Dismiss banner (violations still visible in Runner Audit tab)",
              onClick: function() { setAuditBannerDismissed(true); },
            },
            "× Dismiss",
          ),
        )
      : null,
    h(
      "div",
      { style: { display: "flex", flexDirection: asstPosition === "left" ? "row-reverse" : "row", alignItems: "flex-start", minHeight: "calc(100vh - 56px)" } },
      h(
        "div",
        { className: "main-content", role: "main", style: { flex: 1, minWidth: 0 } },
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
            onOpenDeployment: function () {
              setTab("deployment");
              fetchDeploymentState();
            },
          }),
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
            : tab === "history"
              ? h(HistoryTab, { runs: enrichedRuns, runners: runners })
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
                      : tab === "stats"
                        ? h(StatsTab, null)
                        : tab === "reports"
                          ? h(ReportsTab, {
                              reports: reports,
                              loading: reportsLoading,
                            })
                          : tab === "scheduled-jobs"
                            ? h(ScheduledJobsTab, {
                                data: scheduledJobs,
                                loading: scheduledJobsLoading,
                                onRefresh: fetchScheduledJobs,
                              })
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
                                  ? h(LocalAppsErrorBoundary, { key: "local-apps-boundary" },
                                      h(LocalAppsTab, {
                                        data: localApps,
                                        loading: localAppsLoading,
                                        onRefresh: fetchLocalApps,
                                      })
                                    )
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
                                                ? h(
                                                    "div",
                                                    { className: "section", style: { padding: "24px" } },
                                                    h(
                                                      "div",
                                                      { className: "section-header", style: { marginBottom: "16px", display: "flex", alignItems: "center", justifyContent: "space-between" } },
                                                      h("div", { className: "section-title" },
                                                        h("span", { style: { fontSize: "18px", marginRight: "8px" } }, "⚠️"),
                                                        "Hosted-Runner Billing Audit",
                                                      ),
                                                      h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } },
                                                        runnerAudit.last_checked
                                                          ? h("span", { style: { fontSize: "12px", color: "var(--text-muted)" } },
                                                              "Last checked: " + new Date(runnerAudit.last_checked).toLocaleString(),
                                                            )
                                                          : h("span", { style: { fontSize: "12px", color: "var(--text-muted)" } }, "Not yet checked"),
                                                        h(
                                                          "button",
                                                          {
                                                            className: "btn",
                                                            style: { fontSize: "12px" },
                                                            onClick: triggerRunnerAuditRefresh,
                                                          },
                                                          I.refresh(12),
                                                          " Refresh Now",
                                                        ),
                                                      ),
                                                    ),
                                                    runnerAudit.error
                                                      ? h("div", { style: { color: "var(--accent-red)", marginBottom: "12px", fontSize: "13px" } },
                                                          "Error: " + runnerAudit.error,
                                                        )
                                                      : null,
                                                    (runnerAudit.violations && runnerAudit.violations.length > 0)
                                                      ? h(
                                                          "div",
                                                          null,
                                                          h("div", { style: { marginBottom: "12px", padding: "10px 14px", background: "rgba(248,81,73,0.1)", border: "1px solid rgba(248,81,73,0.3)", borderRadius: "6px", fontSize: "13px" } },
                                                            h("strong", { style: { color: "var(--accent-red)" } }, runnerAudit.violations.length + " violation(s) found. "),
                                                            "These jobs ran on GitHub-hosted runners and may incur unexpected billing costs.",
                                                          ),
                                                          h(
                                                            "div",
                                                            { style: { overflowX: "auto" } },
                                                            h(
                                                              "table",
                                                              { style: { width: "100%", borderCollapse: "collapse", fontSize: "13px" } },
                                                              h(
                                                                "thead",
                                                                null,
                                                                h(
                                                                  "tr",
                                                                  { style: { borderBottom: "1px solid var(--border)", textAlign: "left" } },
                                                                  h("th", { style: { padding: "8px 12px", color: "var(--text-secondary)", fontWeight: "600" } }, "Repo"),
                                                                  h("th", { style: { padding: "8px 12px", color: "var(--text-secondary)", fontWeight: "600" } }, "Workflow"),
                                                                  h("th", { style: { padding: "8px 12px", color: "var(--text-secondary)", fontWeight: "600" } }, "Job"),
                                                                  h("th", { style: { padding: "8px 12px", color: "var(--text-secondary)", fontWeight: "600" } }, "Runner"),
                                                                  h("th", { style: { padding: "8px 12px", color: "var(--text-secondary)", fontWeight: "600" } }, "Started"),
                                                                  h("th", { style: { padding: "8px 12px", color: "var(--text-secondary)", fontWeight: "600" } }, "Link"),
                                                                ),
                                                              ),
                                                              h(
                                                                "tbody",
                                                                null,
                                                                runnerAudit.violations.map(function(v, i) {
                                                                  return h(
                                                                    "tr",
                                                                    { key: i, style: { borderBottom: "1px solid var(--border)", background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)" } },
                                                                    h("td", { style: { padding: "8px 12px", fontWeight: "500" } }, v.repo),
                                                                    h("td", { style: { padding: "8px 12px", color: "var(--text-secondary)" } }, v.workflow),
                                                                    h("td", { style: { padding: "8px 12px", color: "var(--text-secondary)" } }, v.job_name),
                                                                    h("td", { style: { padding: "8px 12px" } },
                                                                      h("span", { style: { background: "rgba(248,81,73,0.15)", color: "var(--accent-red)", padding: "2px 6px", borderRadius: "4px", fontSize: "11px", fontFamily: "monospace" } }, v.runner_name || v.runner_group || "unknown"),
                                                                    ),
                                                                    h("td", { style: { padding: "8px 12px", color: "var(--text-muted)", fontSize: "12px" } },
                                                                      v.started_at ? new Date(v.started_at).toLocaleString() : "—",
                                                                    ),
                                                                    h("td", { style: { padding: "8px 12px" } },
                                                                      v.run_url
                                                                        ? h("a", { href: v.run_url, target: "_blank", rel: "noopener noreferrer", style: { color: "var(--accent-blue)", textDecoration: "none", fontSize: "12px" } }, "View Run ↗")
                                                                        : "—",
                                                                    ),
                                                                  );
                                                                }),
                                                              ),
                                                            ),
                                                          ),
                                                        )
                                                      : h("div", { style: { textAlign: "center", padding: "48px 24px", color: "var(--text-muted)", fontSize: "14px" } },
                                                          h("div", { style: { fontSize: "32px", marginBottom: "12px" } }, "✓"),
                                                          runnerAudit.last_checked
                                                            ? "No hosted-runner violations detected. All recent jobs ran on self-hosted runners."
                                                            : "Audit has not run yet. Click \"Refresh Now\" to trigger an immediate check.",
                                                        ),
                                                  )
                                                : tab === "principals"
                                                  ? h(PrincipalsTab, null)
                                                  : null,
      ),
      h(AssistantSidebar, { currentTab: tab, open: asstOpen, onToggle: toggleAsst }),
    ),
    recoveryModal && recoveryModal.visible
      ? h(
          "div",
          {
            style: {
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.5)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1000,
              cursor: "pointer",
            },
            onClick: function (e) {
              if (e.target === e.currentTarget) setRecoveryModal(null);
            },
          },
          h(
            "div",
            {
              style: {
                background: "var(--bg-primary)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "24px",
                minWidth: 400,
                maxWidth: 560,
                maxHeight: "80vh",
                overflow: "auto",
                cursor: "default",
                boxShadow: "0 10px 40px rgba(0,0,0,0.3)",
              },
              onClick: function (e) {
                e.stopPropagation();
              },
            },
            h("h2", { style: { margin: "0 0 16px 0", color: "var(--accent-red)" } }, "Backend Not Responding"),
            (function () {
              var isWindows = typeof navigator !== "undefined" && navigator.platform.includes("Win32");
              var isMac = typeof navigator !== "undefined" && navigator.platform.includes("Mac");
              return h(
                "div",
                null,
                isWindows || isMac
                  ? h("p", { style: { margin: "0 0 16px 0", color: "var(--text-secondary)", fontSize: "14px" } }, "The dashboard backend is not responding. Click \"Start Now\" to restart the service, or run the terminal command below.")
                  : h(
                      "div",
                      null,
                      h("p", { style: { margin: "0 0 12px 0", color: "var(--text-secondary)", fontSize: "14px" } }, "The dashboard backend is not responding. To restart the service, run this command in a terminal:"),
                      h("pre", { style: { background: "var(--bg-secondary)", padding: "12px", borderRadius: 4, margin: "0 0 16px 0", fontSize: "13px", overflow: "auto", color: "var(--text-primary)" } }, "systemctl --user restart runner-dashboard\n\nThen refresh this page."),
                    ),
              );
            })(),
            h(
              "div",
              { style: { display: "flex", gap: "12px", justifyContent: "flex-end" } },
              (function () {
                var isWindows = typeof navigator !== "undefined" && navigator.platform.includes("Win32");
                var isMac = typeof navigator !== "undefined" && navigator.platform.includes("Mac");
                return isWindows || isMac
                  ? h(
                      "button",
                      {
                        onClick: function () {
                          if (window.location.protocol === "https:") {
                            window.location.href = "runner-dashboard://start";
                          } else {
                            alert("Protocol handler requires HTTPS context. Make sure you're using HTTPS.");
                          }
                        },
                        style: {
                          padding: "8px 16px",
                          background: "var(--accent-green)",
                          color: "white",
                          border: "none",
                          borderRadius: 4,
                          cursor: "pointer",
                          fontSize: "14px",
                          fontWeight: "500",
                        },
                      },
                      "Start Now",
                    )
                  : null;
              })(),
              h(
                "button",
                {
                  onClick: function () { setRecoveryModal(null); },
                  style: {
                    padding: "8px 16px",
                    background: "var(--bg-secondary)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    cursor: "pointer",
                    fontSize: "14px",
                  },
                },
                "Refresh",
              ),
            ),
          ),
        )
      : null,
    h(DashboardHelp, { currentTab: tab }),
  );
}

export default App;
