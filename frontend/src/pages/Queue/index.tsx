import React from "react";

const h = React.createElement;

// ════════════════════════ HELPERS ════════════════════════

function formatDuration(s: number): string {
  if (!s || s < 0) return "-";
  if (s < 60) return s + "s";
  return Math.floor(s / 60) + "m " + (s % 60) + "s";
}

function sortStateNext(current: any, key: string): any {
  if (current && current.key === key) {
    return { key: key, dir: current.dir === "asc" ? "desc" : "asc" };
  }
  return { key: key, dir: "asc" };
}

function normalizeSortValue(value: any): any {
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
  return !Number.isNaN(numeric) ? numeric : text;
}

function sortRows(rows: any[], sort: any, accessors: any): any[] {
  if (!sort || !sort.key || !accessors || !accessors[sort.key]) {
    return rows.slice();
  }
  var dir = sort.dir === "desc" ? -1 : 1;
  return rows
    .map(function (row, index) {
      return { row: row, index: index };
    })
    .sort(function (a: any, b: any) {
      var av = normalizeSortValue(accessors[sort.key](a.row));
      var bv = normalizeSortValue(accessors[sort.key](b.row));
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return a.index - b.index;
    })
    .map(function (entry: any) {
      return entry.row;
    });
}

function Stat(p: any) {
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

function SortTh(p: any) {
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
    onKeyDown: function (e: any) {
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

function Collapse(p: any) {
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
        h("span", null, o ? "▼" : "▶"),
      ),
    ),
    h(
      "div",
      { className: "section-body" + (o ? "" : " collapsed") },
      p.children,
    ),
  );
}

// ════════════════════════ QUEUE TAB ════════════════════════

interface QueueTabProps {
  queue: any;
  loading: boolean;
  onRefresh?: () => void;
}

export function QueueTab(p: QueueTabProps) {
  var q = p.queue || {};
  var loading = p.loading;
  var onRefresh = p.onRefresh;
  var ip = q.in_progress || [];
  var qu = q.queued || [];
  var ds = React.useState(null);
  var diag = ds[0],
    setDiag = ds[1];
  var dl = React.useState(false);
  var diagLoading = dl[0],
    setDiagLoading = dl[1];
  var cs = React.useState({});
  var cancelling = cs[0],
    setCancelling = cs[1];
  // Two-step inline confirmation state for destructive actions (issue #7)
  var cwcs = React.useState(null);
  var confirmWorkflow = cwcs[0],
    setConfirmWorkflow = cwcs[1];
  var crcs = React.useState(null);
  var confirmRun = crcs[0],
    setConfirmRun = crcs[1];
  // Inline status message replaces alert() (issue #51)
  var cms = React.useState(null);
  var cancelMsg = cms[0],
    setCancelMsg = cms[1];
  var ipSortState = React.useState({ key: "runningFor", dir: "desc" });
  var ipSort = ipSortState[0],
    setIpSort = ipSortState[1];
  var queueSortState = React.useState({ key: "waiting", dir: "desc" });
  var queueSort = queueSortState[0],
    setQueueSort = queueSortState[1];
  function elapsed(r: any) {
    var start = r.run_started_at || r.created_at;
    if (!start) return "-";
    return formatDuration(
      Math.round((Date.now() - new Date(start).getTime()) / 1000),
    );
  }
  function waited(r: any) {
    if (!r.created_at) return "-";
    var s = Math.round(
      (Date.now() - new Date(r.created_at).getTime()) / 1000,
    );
    return formatDuration(s);
  }
  function waitColor(r: any) {
    var s = Math.round(
      (Date.now() - new Date(r.created_at || 0).getTime()) / 1000,
    );
    return s > 300
      ? "var(--accent-red)"
      : s > 60
        ? "var(--accent-yellow)"
        : "inherit";
  }
  function runRepo(r: any) {
    return (r.repository && r.repository.name) || "";
  }
  function runRunner(r: any) {
    return r.runner_name || (r.runner && r.runner.name) || "-";
  }
  function elapsedSeconds(r: any) {
    var start = r.run_started_at || r.created_at;
    return start
      ? Math.round((Date.now() - new Date(start).getTime()) / 1000)
      : 0;
  }
  function waitingSeconds(r: any) {
    return r.created_at
      ? Math.round((Date.now() - new Date(r.created_at).getTime()) / 1000)
      : 0;
  }
  var runAccessors = {
    workflow: function (r: any) {
      return r.name;
    },
    repo: runRepo,
    branch: function (r: any) {
      return r.head_branch;
    },
    runner: runRunner,
    runningFor: elapsedSeconds,
    waiting: waitingSeconds,
  };
  var sortedIp = sortRows(ip, ipSort, runAccessors);
  var sortedQu = sortRows(qu, queueSort, runAccessors);
  var staleQu = sortedQu.filter(function (r: any) {
    return waitingSeconds(r) > 300;
  });
  function runDiagnose() {
    setDiagLoading(true);
    setDiag(null);
    fetch("/api/queue/diagnose")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setDiag(d);
        setDiagLoading(false);
      })
      .catch(function () {
        setDiagLoading(false);
      });
  }
  function cancelRun(repo: string, runId: any) {
    var key = repo + "/" + runId;
    setCancelling(function (prev: any) {
      var n = Object.assign({}, prev);
      n[key] = "pending";
      return n;
    });
    fetch("/api/runs/" + repo + "/cancel/" + runId, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) {
        return r.json();
      })
      .then(function () {
        setCancelling(function (prev: any) {
          var n = Object.assign({}, prev);
          n[key] = "done";
          return n;
        });
        if (onRefresh) setTimeout(onRefresh, 1500);
      })
      .catch(function () {
        setCancelling(function (prev: any) {
          var n = Object.assign({}, prev);
          n[key] = "error";
          return n;
        });
      });
  }
  function cancelWorkflow(workflowName: string, repo?: string) {
    // Two-step inline confirmation: first call arms, second call fires (issue #7)
    var key = workflowName + (repo ? "/" + repo : "");
    if (confirmWorkflow !== key) {
      setConfirmWorkflow(key);
      setTimeout(function () {
        setConfirmWorkflow(function (cur: any) {
          return cur === key ? null : cur;
        });
      }, 5000);
      return;
    }
    setConfirmWorkflow(null);
    fetch("/api/queue/cancel-workflow", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({
        workflow_name: workflowName,
        repo: repo || null,
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (d: any) {
        var msg =
          "Cancelled " +
          d.cancelled_count +
          " run(s)" +
          (d.errors && d.errors.length > 0
            ? " — Errors: " + d.errors.join(", ")
            : "");
        setCancelMsg({ type: "success", text: msg });
        setTimeout(function () { setCancelMsg(null); }, 6000);
        if (onRefresh) setTimeout(onRefresh, 1500);
      })
      .catch(function () {
        setCancelMsg({ type: "error", text: "Cancel request failed" });
        setTimeout(function () { setCancelMsg(null); }, 6000);
      });
  }
  // Group queued runs by workflow name for bulk-cancel
  var workflowGroups: any = {};
  qu.forEach(function (r: any) {
    var n = r.name || "?";
    if (!workflowGroups[n]) workflowGroups[n] = [];
    workflowGroups[n].push(r);
  });
  var bulkTargets = Object.keys(workflowGroups).filter(function (n: string) {
    return workflowGroups[n].length > 1;
  });
  return h(
    "div",
    null,
    cancelMsg
      ? h(
          "div",
          {
            role: "alert",
            style: {
              margin: "0 0 12px",
              padding: "10px 16px",
              borderRadius: 6,
              background:
                cancelMsg.type === "error"
                  ? "rgba(248,81,73,0.15)"
                  : "rgba(63,185,80,0.15)",
              color:
                cancelMsg.type === "error"
                  ? "var(--accent-red)"
                  : "var(--accent-green)",
              border:
                "1px solid " +
                (cancelMsg.type === "error"
                  ? "var(--accent-red)"
                  : "var(--accent-green)"),
              fontSize: 13,
            },
          },
          cancelMsg.text,
        )
      : null,
    h(
      "div",
      { className: "stat-row" },
      h(Stat, {
        label: "In Progress",
        value: ip.length,
        color: ip.length > 0 ? "var(--accent-yellow)" : "inherit",
        sub: ip.length > 0 ? "actively running" : "idle",
      }),
      h(Stat, {
        label: "Queued",
        value: qu.length,
        color: qu.length > 0 ? "var(--accent-blue)" : "inherit",
        sub: qu.length > 0 ? "waiting for runner" : "empty",
      }),
      h(Stat, {
        label: "Total Active",
        value: q.total || 0,
        sub: "across all repos",
      }),
      h(Stat, {
        label: "Auto-refresh",
        value: "15s",
        sub: "updates automatically",
      }),
    ),
    h(
      "div",
      { className: "mobile-kpi-strip", "aria-label": "Queue health summary" },
      [
        { label: "Queued", value: qu.length },
        { label: "Running", value: ip.length },
        { label: "Stale", value: staleQu.length },
      ].map(function (item: any) {
        return h(
          "div",
          { key: item.label, className: "mobile-kpi" },
          h("div", { className: "mobile-kpi-label" }, item.label),
          h("div", { className: "mobile-kpi-value" }, item.value),
        );
      }),
    ),
    qu.length > 0
      ? h(
          "div",
          { style: { padding: "0 0 12px 0" } },
          h(
            "button",
            {
              className: "btn",
              onClick: runDiagnose,
              disabled: diagLoading,
              style: { marginRight: 8 },
            },
            diagLoading ? h("span", { className: "spinner" }) : "🔍",
            " Why are jobs waiting?",
          ),
          diag &&
            h(
              "div",
              {
                style: {
                  marginTop: 10,
                  padding: "12px 16px",
                  background: "var(--bg-secondary)",
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                },
              },
              h(
                "div",
                {
                  style: {
                    fontWeight: 600,
                    marginBottom: 6,
                    color:
                      diag.pick_runner_misconfig &&
                      diag.pick_runner_misconfig.length > 0
                        ? "var(--accent-red)"
                        : diag.runner_groups_restricted &&
                            diag.waiting_for_generic_self_hosted > 0
                          ? "var(--accent-red)"
                          : diag.waiting_for_self_hosted > 0 &&
                              diag.runner_pool &&
                              diag.runner_pool.idle === 0
                            ? "var(--accent-yellow)"
                            : diag.waiting_for_github_hosted > 0
                              ? "var(--accent-blue)"
                              : "var(--accent-green)",
                  },
                },
                diag.bottleneck,
              ),
              h(
                "div",
                {
                  style: {
                    display: "flex",
                    gap: 16,
                    marginTop: 8,
                    flexWrap: "wrap",
                  },
                },
                h(
                  "span",
                  { style: { fontSize: 12, color: "var(--text-muted)" } },
                  "Fleet runners — ",
                  h(
                    "b",
                    null,
                    (diag.runner_pool && diag.runner_pool.busy) || 0,
                  ),
                  " busy / ",
                  h(
                    "b",
                    null,
                    (diag.runner_pool && diag.runner_pool.idle) || 0,
                  ),
                  " idle / ",
                  h(
                    "b",
                    null,
                    (diag.runner_pool && diag.runner_pool.offline) || 0,
                  ),
                  " offline",
                ),
                h(
                  "span",
                  { style: { fontSize: 12, color: "var(--text-muted)" } },
                  "Waiting (d-sorg-fleet): ",
                  h(
                    "b",
                    { style: { color: "var(--accent-yellow)" } },
                    diag.waiting_for_fleet || 0,
                  ),
                ),
                h(
                  "span",
                  { style: { fontSize: 12, color: "var(--text-muted)" } },
                  "Waiting (generic self-hosted): ",
                  h(
                    "b",
                    {
                      style: {
                        color:
                          diag.waiting_for_generic_self_hosted > 0
                            ? "var(--accent-orange)"
                            : "inherit",
                      },
                    },
                    diag.waiting_for_generic_self_hosted || 0,
                  ),
                ),
                h(
                  "span",
                  { style: { fontSize: 12, color: "var(--text-muted)" } },
                  "Waiting (GitHub-hosted): ",
                  h(
                    "b",
                    { style: { color: "var(--accent-blue)" } },
                    diag.waiting_for_github_hosted || 0,
                  ),
                ),
              ),
              diag.runner_groups && diag.runner_groups.length > 0
                ? h(
                    "div",
                    { style: { marginTop: 10 } },
                    diag.runner_groups.map(function (g: any, i: number) {
                      var hasRunners = g.runner_count > 0;
                      var hasBlocked =
                        g.blocked_waiting_repos &&
                        g.blocked_waiting_repos.length > 0;
                      return h(
                        "div",
                        {
                          key: i,
                          style: {
                            marginBottom: 6,
                            padding: "8px 10px",
                            borderRadius: 6,
                            fontSize: 12,
                            border:
                              "1px solid " +
                              (hasBlocked
                                ? "var(--accent-red)"
                                : g.restricted
                                  ? "var(--accent-orange)"
                                  : "var(--border)"),
                            background: hasBlocked
                              ? "rgba(248,81,73,0.08)"
                              : g.restricted
                                ? "rgba(240,136,62,0.08)"
                                : "var(--bg-tertiary)",
                          },
                        },
                        h(
                          "div",
                          {
                            style: {
                              display: "flex",
                              gap: 8,
                              alignItems: "center",
                              flexWrap: "wrap",
                            },
                          },
                          h(
                            "b",
                            {
                              style: {
                                color: hasBlocked
                                  ? "var(--accent-red)"
                                  : g.restricted
                                    ? "var(--accent-orange)"
                                    : "var(--accent-green)",
                              },
                            },
                            g.name,
                          ),
                          g.inherited &&
                            h(
                              "span",
                              {
                                style: {
                                  fontSize: 10,
                                  padding: "1px 5px",
                                  borderRadius: 3,
                                  background: "rgba(88,166,255,0.15)",
                                  color: "var(--accent-blue)",
                                  border:
                                    "1px solid rgba(88,166,255,0.3)",
                                  fontWeight: 600,
                                },
                              },
                              "ENTERPRISE",
                            ),
                          !g.inherited &&
                            h(
                              "span",
                              {
                                style: {
                                  fontSize: 10,
                                  padding: "1px 5px",
                                  borderRadius: 3,
                                  background: "rgba(63,185,80,0.1)",
                                  color: "var(--accent-green)",
                                  border: "1px solid rgba(63,185,80,0.3)",
                                  fontWeight: 600,
                                },
                              },
                              "ORG",
                            ),
                          h(
                            "span",
                            { style: { color: "var(--text-muted)" } },
                            g.visibility,
                          ),
                          hasRunners &&
                            h(
                              "span",
                              {
                                style: { color: "var(--text-secondary)" },
                              },
                              g.runner_count,
                              " runner" +
                                (g.runner_count !== 1 ? "s" : ""),
                              g.runner_names && g.runner_names.length > 0
                                ? ": " + g.runner_names.join(", ")
                                : "",
                            ),
                          !hasRunners &&
                            h(
                              "span",
                              { style: { color: "var(--text-muted)" } },
                              "no runners",
                            ),
                        ),
                        hasBlocked &&
                          h(
                            "div",
                            {
                              style: {
                                marginTop: 4,
                                color: "var(--accent-red)",
                              },
                            },
                            g.inherited
                              ? "Enterprise group is restricted — these repos cannot access it (or any org runners it gates): "
                              : "Blocking repos: ",
                            g.blocked_waiting_repos.join(", "),
                            g.inherited
                              ? " — fix: change enterprise group visibility to 'All repositories' in GitHub Enterprise settings"
                              : " — these jobs cannot reach the runners in this group",
                          ),
                        g.restricted &&
                          g.allowed_repos &&
                          g.allowed_repos.length > 0 &&
                          !hasBlocked &&
                          h(
                            "div",
                            {
                              style: {
                                marginTop: 4,
                                color: "var(--text-muted)",
                              },
                            },
                            "Allowed: ",
                            g.allowed_repos.join(", "),
                          ),
                      );
                    }),
                  )
                : null,
              diag.sampled_jobs && diag.sampled_jobs.length > 0
                ? h(
                    "details",
                    { style: { marginTop: 8 } },
                    h(
                      "summary",
                      {
                        style: {
                          fontSize: 12,
                          cursor: "pointer",
                          color: "var(--text-secondary)",
                        },
                      },
                      "Job details (",
                      diag.jobs_sampled,
                      " sampled)",
                    ),
                    h(
                      "table",
                      {
                        className: "data-table",
                        style: { marginTop: 6 },
                      },
                      h(
                        "thead",
                        null,
                        h(
                          "tr",
                          null,
                          h("th", null, "Repo"),
                          h("th", null, "Job"),
                          h("th", null, "Target runner"),
                          h("th", null, "Labels"),
                        ),
                      ),
                      h(
                        "tbody",
                        null,
                        (diag.sampled_jobs || []).map(function (j: any, i: number) {
                          var targetColor =
                            j.target === "self-hosted (d-sorg-fleet)"
                              ? "var(--accent-yellow)"
                              : j.target === "self-hosted (generic)"
                                ? "var(--accent-orange)"
                                : j.target === "github-hosted"
                                  ? "var(--accent-blue)"
                                  : "var(--accent-red)";
                          return h(
                            "tr",
                            { key: i },
                            h("td", null, j.repo),
                            h("td", null, j.job),
                            h(
                              "td",
                              {
                                style: {
                                  color: targetColor,
                                  fontSize: 12,
                                },
                              },
                              j.target,
                            ),
                            h(
                              "td",
                              {
                                style: {
                                  fontSize: 11,
                                  color: "var(--text-muted)",
                                },
                              },
                              j.labels && j.labels.join(", "),
                            ),
                          );
                        }),
                      ),
                    ),
                  )
                : null,
            ),
        )
      : null,
    loading && ip.length === 0 && qu.length === 0
      ? h(
          "div",
          {
            style: {
              textAlign: "center",
              padding: 40,
              color: "var(--text-muted)",
            },
          },
          h("span", { className: "spinner" }),
          " Loading queue...",
        )
      : null,
    h(
      Collapse,
      {
        title: "In Progress",
        icon: h("span", {
          className: "queue-dot active",
          style: { marginRight: 4 },
        }),
        badge: ip.length + " running",
        defaultOpen: true,
      },
      ip.length > 0
        ? h(
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
                  h("th", null, ""),
                  h(SortTh, {
                    label: "Workflow",
                    sortKey: "workflow",
                    sort: ipSort,
                    setSort: setIpSort,
                  }),
                  h(SortTh, {
                    label: "Repo",
                    sortKey: "repo",
                    sort: ipSort,
                    setSort: setIpSort,
                  }),
                  h(SortTh, {
                    label: "Branch",
                    sortKey: "branch",
                    sort: ipSort,
                    setSort: setIpSort,
                  }),
                  h(SortTh, {
                    label: "Runner",
                    sortKey: "runner",
                    sort: ipSort,
                    setSort: setIpSort,
                  }),
                  h(SortTh, {
                    label: "Running for",
                    sortKey: "runningFor",
                    sort: ipSort,
                    setSort: setIpSort,
                  }),
                  h("th", null, ""),
                ),
              ),
              h(
                "tbody",
                null,
                sortedIp.map(function (r: any) {
                  var repo = (r.repository && r.repository.name) || "";
                  var key = repo + "/" + r.id;
                  var cstate = cancelling[key];
                  var runner =
                    r.runner_name || (r.runner && r.runner.name) || "-";
                  return h(
                    "tr",
                    { key: r.id },
                    h(
                      "td",
                      null,
                      h(
                        "span",
                        { className: "conclusion-badge in_progress" },
                        "running",
                      ),
                    ),
                    h("td", null, r.name),
                    h("td", null, repo),
                    h(
                      "td",
                      { style: { color: "var(--text-secondary)" } },
                      r.head_branch,
                    ),
                    h(
                      "td",
                      {
                        style: {
                          color: "var(--text-muted)",
                          fontSize: 12,
                        },
                      },
                      runner,
                    ),
                    h("td", null, elapsed(r)),
                    h(
                      "td",
                      {
                        style: {
                          display: "flex",
                          gap: 6,
                          alignItems: "center",
                        },
                      },
                      h(
                        "a",
                        {
                          href: r.html_url,
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
                      repo &&
                        h(
                          "button",
                          {
                            // Two-step inline confirmation for destructive run cancel (issue #7)
                            onClick: (function (runId: any) {
                              return function () {
                                var rkey = repo + "/" + runId;
                                if (confirmRun !== rkey) {
                                  setConfirmRun(rkey);
                                  setTimeout(function () {
                                    setConfirmRun(function (cur: any) {
                                      return cur === rkey ? null : cur;
                                    });
                                  }, 5000);
                                } else {
                                  setConfirmRun(null);
                                  cancelRun(repo, runId);
                                }
                              };
                            })(r.id),
                            disabled: !!cstate,
                            style: {
                              fontSize: 11,
                              padding: "2px 7px",
                              background:
                                confirmRun === repo + "/" + r.id
                                  ? "var(--accent-red)"
                                  : "none",
                              border: "1px solid var(--accent-red)",
                              color:
                                cstate === "done"
                                  ? "var(--text-muted)"
                                  : confirmRun === repo + "/" + r.id
                                    ? "#fff"
                                    : "var(--accent-red)",
                              borderRadius: 4,
                              cursor: cstate ? "default" : "pointer",
                            },
                          },
                          cstate === "pending"
                            ? h("span", { className: "spinner" })
                            : cstate === "done"
                              ? "cancelled"
                              : confirmRun === repo + "/" + r.id
                                ? "Click again to confirm"
                                : "Cancel",
                        ),
                    ),
                  );
                }),
              ),
            ),
          )
        : h(
            "div",
            {
              style: {
                color: "var(--text-muted)",
                padding: 20,
                textAlign: "center",
              },
            },
            "No runs currently in progress",
          ),
    ),
    h(
      Collapse,
      {
        title: "Queued",
        icon: h("span", {
          className: "queue-dot waiting",
          style: { marginRight: 4 },
        }),
        badge: qu.length + " waiting",
        defaultOpen: true,
      },
      qu.length > 0
        ? h(
            "div",
            null,
            bulkTargets.length > 0
              ? h(
                  "div",
                  {
                    style: {
                      padding: "8px 0",
                      display: "flex",
                      gap: 8,
                      flexWrap: "wrap",
                      alignItems: "center",
                    },
                  },
                  h(
                    "span",
                    {
                      style: { fontSize: 12, color: "var(--text-muted)" },
                    },
                    "Bulk cancel:",
                  ),
                  bulkTargets.map(function (name: string) {
                    // Two-step inline confirmation for bulk workflow cancel (issue #7)
                    var isPending = confirmWorkflow === name;
                    return h(
                      "button",
                      {
                        key: name,
                        onClick: function () {
                          cancelWorkflow(name);
                        },
                        style: {
                          fontSize: 11,
                          padding: "3px 10px",
                          background: isPending
                            ? "var(--accent-red)"
                            : "none",
                          border: isPending
                            ? "1px solid var(--accent-red)"
                            : "1px solid var(--accent-orange)",
                          color: isPending
                            ? "#fff"
                            : "var(--accent-orange)",
                          borderRadius: 4,
                          cursor: "pointer",
                        },
                      },
                      isPending
                        ? "Click again to confirm"
                        : h(
                            React.Fragment,
                            null,
                            "Cancel all '",
                            name,
                            "' (",
                            workflowGroups[name].length,
                            ")",
                          ),
                    );
                  }),
                )
              : null,
            h(
              "div",
              { className: "queue-desktop-table", style: { overflowX: "auto" } },
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
                    h(SortTh, {
                      label: "Workflow",
                      sortKey: "workflow",
                      sort: queueSort,
                      setSort: setQueueSort,
                    }),
                    h(SortTh, {
                      label: "Repo",
                      sortKey: "repo",
                      sort: queueSort,
                      setSort: setQueueSort,
                    }),
                    h(SortTh, {
                      label: "Branch",
                      sortKey: "branch",
                      sort: queueSort,
                      setSort: setQueueSort,
                    }),
                    h(SortTh, {
                      label: "Waiting",
                      sortKey: "waiting",
                      sort: queueSort,
                      setSort: setQueueSort,
                    }),
                    h("th", null, ""),
                  ),
                ),
                h(
                  "tbody",
                  null,
                  sortedQu.map(function (r: any, idx: number) {
                    var repo = (r.repository && r.repository.name) || "";
                    var key = repo + "/" + r.id;
                    var cstate = cancelling[key];
                    return h(
                      "tr",
                      {
                        key: r.id,
                        style: { opacity: cstate === "done" ? 0.4 : 1 },
                      },
                      h(
                        "td",
                        {
                          style: {
                            color: "var(--text-muted)",
                            fontVariantNumeric: "tabular-nums",
                            fontSize: 12,
                          },
                        },
                        idx + 1,
                      ),
                      h("td", null, r.name),
                      h("td", null, repo),
                      h(
                        "td",
                        { style: { color: "var(--text-secondary)" } },
                        r.head_branch,
                      ),
                      h(
                        "td",
                        {
                          style: {
                            color: waitColor(r),
                            fontVariantNumeric: "tabular-nums",
                          },
                        },
                        waited(r),
                      ),
                      h(
                        "td",
                        {
                          style: {
                            display: "flex",
                            gap: 6,
                            alignItems: "center",
                          },
                        },
                        h(
                          "a",
                          {
                            href: r.html_url,
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
                        repo &&
                          h(
                            "button",
                            {
                              onClick: function () {
                                var rkey = repo + "/" + r.id;
                                if (confirmRun !== rkey) {
                                  setConfirmRun(rkey);
                                  setTimeout(function () {
                                    setConfirmRun(function (cur: any) {
                                      return cur === rkey ? null : cur;
                                    });
                                  }, 5000);
                                } else {
                                  setConfirmRun(null);
                                  cancelRun(repo, r.id);
                                }
                              },
                              disabled: !!cstate,
                              style: {
                                fontSize: 11,
                                padding: "2px 7px",
                                background:
                                  confirmRun === repo + "/" + r.id
                                    ? "var(--accent-red)"
                                    : "none",
                                border: "1px solid var(--accent-red)",
                                color:
                                  cstate === "done"
                                    ? "var(--text-muted)"
                                    : confirmRun === repo + "/" + r.id
                                      ? "#fff"
                                      : "var(--accent-red)",
                                borderRadius: 4,
                                cursor: cstate ? "default" : "pointer",
                              },
                            },
                            cstate === "pending"
                              ? h("span", { className: "spinner" })
                              : cstate === "done"
                                ? "cancelled"
                                : confirmRun === repo + "/" + r.id
                                  ? "Confirm cancel (1)"
                                  : "Cancel",
                          ),
                      ),
                    );
                  }),
                ),
              ),
            ),
            h(
              "div",
              { className: "mobile-card-list", "aria-label": "Stale queued runs" },
              (staleQu.length > 0 ? staleQu : sortedQu).map(function (r: any) {
                var repo = (r.repository && r.repository.name) || "";
                var key = repo + "/" + r.id;
                var cstate = cancelling[key];
                var isConfirming = confirmRun === key;
                return h(
                  "div",
                  {
                    key: "mobile-" + r.id,
                    className: "mobile-run-card",
                    style: { opacity: cstate === "done" ? 0.45 : 1 },
                  },
                  h(
                    "div",
                    { className: "mobile-run-title" },
                    h("span", { className: "queue-dot waiting" }),
                    h("span", { className: "mobile-run-name" }, r.name || "?"),
                  ),
                  h(
                    "div",
                    { className: "mobile-run-meta" },
                    h("span", null, repo || "unknown repo"),
                    h("span", null, r.head_branch || "unknown branch"),
                    h("span", { style: { color: waitColor(r) } }, waited(r)),
                  ),
                  h(
                    "div",
                    { className: "mobile-run-actions" },
                    h(
                      "a",
                      {
                        href: r.html_url,
                        target: "_blank",
                        rel: "noopener",
                        style: {
                          color: "var(--accent-blue)",
                          textDecoration: "none",
                          fontSize: 12,
                        },
                      },
                      "View run",
                    ),
                    repo
                      ? h(
                          "button",
                          {
                            className: "btn",
                            onClick: function () {
                              if (!isConfirming) {
                                setConfirmRun(key);
                                setTimeout(function () {
                                  setConfirmRun(function (cur: any) {
                                    return cur === key ? null : cur;
                                  });
                                }, 5000);
                              } else {
                                setConfirmRun(null);
                                cancelRun(repo, r.id);
                              }
                            },
                            disabled: !!cstate,
                            style: {
                              background: isConfirming
                                ? "var(--accent-red)"
                                : "var(--bg-secondary)",
                              border: "1px solid var(--accent-red)",
                              color: isConfirming ? "#fff" : "var(--accent-red)",
                            },
                          },
                          cstate === "pending"
                            ? h("span", { className: "spinner" })
                            : cstate === "done"
                              ? "Cancelled"
                              : isConfirming
                                ? "Confirm cancel (1)"
                                : "Cancel",
                        )
                      : null,
                  ),
                );
              }),
            ),
          )
        : h(
            "div",
            {
              style: {
                color: "var(--text-muted)",
                padding: 20,
                textAlign: "center",
              },
            },
            "Queue is empty — all runners idle",
          ),
    ),
  );
}
