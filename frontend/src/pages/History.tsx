/**
 * History.tsx — the "History" sub-tab (under Analysis), extracted (behaviour
 * 1:1) from the legacy `App.tsx` monolith as part of the decomposition epic
 * (#836, pass 10).
 *
 * Renders the recent-workflow-run table: status-icon column, sortable
 * Workflow/Repository/Branch/Machine/Duration/When headers, a status filter
 * bar with live counts, and a 50-row cap. Rows are click-through to the run's
 * GitHub URL via the trusted-origin `safeOpen` guard.
 *
 * Presentational: `runs` (and `runners`) are owned by the legacy App and passed
 * in as props. Sort logic comes from `decompSort`, the sortable header from
 * `decompSortTh`, and the URL guard from the shared `formatters` module.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 1:1 port of dynamically-typed legacy workflow-run payloads; the backend response shapes lack complete TypeScript definitions. */
import React from "react";
import { safeOpen } from "../components/formatters";
import { sortRows, type SortState } from "./decompSort";
import { SortTh } from "./decompSortTh";

const h = React.createElement;

const MACHINE_COLORS: Record<string, string> = {
  ControlTower: "var(--accent-purple)",
  DeskComputer: "var(--accent-blue)",
  Oglaptop: "var(--accent-orange)",
  GitHub: "var(--text-muted)",
};

export function HistoryTab(props: {
  runs?: any[];
  runners?: any[];
}): React.ReactElement {
  const runs = props.runs || [];
  const fs = React.useState("all");
  const filter = fs[0],
    setFilter = fs[1];
  const sortState = React.useState<SortState>({ key: "when", dir: "desc" });
  const historySort = sortState[0],
    setHistorySort = sortState[1];
  const filtered = runs.filter(function (r: any) {
    if (filter === "all") return true;
    if (filter === "success") return r.conclusion === "success";
    if (filter === "failure") return r.conclusion === "failure";
    if (filter === "running") return r.status === "in_progress";
    if (filter === "cancelled") return r.conclusion === "cancelled";
    return true;
  });
  function dur(r: any) {
    if (!r.run_started_at || !r.updated_at) return "-";
    const ms =
      (new Date(r.updated_at) as any) - (new Date(r.run_started_at) as any);
    const s = Math.floor(ms / 1000);
    if (s < 60) return s + "s";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m " + (s % 60) + "s";
    return Math.floor(m / 60) + "h " + (m % 60) + "m";
  }
  function ago(d: any) {
    if (!d) return "-";
    const s = Math.floor((Date.now() - (new Date(d) as any)) / 1000);
    if (s < 60) return s + "s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    const hr = Math.floor(m / 60);
    if (hr < 24) return hr + "h ago";
    return Math.floor(hr / 24) + "d ago";
  }
  function statusIcon(r: any) {
    if (r.status === "in_progress")
      return h("span", { style: { color: "var(--accent-yellow)" } }, "●");
    if (r.conclusion === "success")
      return h("span", { style: { color: "var(--accent-green)" } }, "✓");
    if (r.conclusion === "failure")
      return h("span", { style: { color: "var(--accent-red)" } }, "✗");
    if (r.conclusion === "cancelled")
      return h("span", { style: { color: "var(--text-muted)" } }, "○");
    return h("span", { style: { color: "var(--text-muted)" } }, "•");
  }
  const historyAccessors = {
    status: function (r: any) {
      return r.status === "in_progress" ? "running" : r.conclusion || "";
    },
    workflow: function (r: any) {
      return r.name;
    },
    repository: function (r: any) {
      return (r.repository || {}).name || "";
    },
    branch: function (r: any) {
      return r.head_branch;
    },
    machine: function (r: any) {
      return r.machine_name || "";
    },
    duration: function (r: any) {
      if (!r.run_started_at || !r.updated_at) return 0;
      return (
        (new Date(r.updated_at) as any) - (new Date(r.run_started_at) as any)
      );
    },
    when: function (r: any) {
      return r.created_at || r.updated_at || "";
    },
  };
  const sortedFiltered = sortRows(filtered, historySort, historyAccessors);
  const counts: Record<string, number> = {
    all: runs.length,
    success: runs.filter(function (r: any) {
      return r.conclusion === "success";
    }).length,
    failure: runs.filter(function (r: any) {
      return r.conclusion === "failure";
    }).length,
    running: runs.filter(function (r: any) {
      return r.status === "in_progress";
    }).length,
    cancelled: runs.filter(function (r: any) {
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
      ["all", "success", "failure", "running", "cancelled"].map(function (f) {
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
      }),
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
        sortedFiltered.slice(0, 50).map(function (r: any) {
          const machine = r.machine_name || "-";
          const mColor = MACHINE_COLORS[machine] || "var(--text-muted)";
          const repo = (r.repository || {}).name || "?";
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

export default HistoryTab;
