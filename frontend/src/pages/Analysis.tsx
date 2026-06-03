/**
 * Analysis.tsx — the self-contained sub-tabs of the legacy "Analysis" view,
 * extracted (behaviour-wise 1:1) from the legacy `App.tsx` monolith as part of
 * the decomposition epic (#836, pass 7).
 *
 * Four leaf panels live here; the legacy `AnalysisTab` orchestrator (which also
 * fans out to the still-legacy `HistoryTab`) imports and renders them:
 *   - StatsTab            workflow duration percentiles + p50/p95 sparkline
 *   - PerformanceTab      Web-Vitals percentiles by route
 *   - AnalysisOutcomesTab job-placement outcomes by workflow × machine
 *   - ReportsTab          fleet assessment report viewer (markdown)
 *
 * Each panel owns its own data fetch via `legacyFetch`, matching the original
 * legacy behaviour exactly (these were never props-driven for data). Shared
 * presentational bits (`Stat`) come from `../components`.
 */
import React, { useEffect, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { Stat } from "../components/Stat";
import { SubTabs } from "../components/SubTabs";
import { legacyFetch } from "../lib/api";
import { isAnalysisTabKey } from "../lib/analysisTabs";
import { HistoryTab } from "./History";

// ── Shared helper ────────────────────────────────────────────────────────────

/** Formats a duration in seconds as "Ns" / "N.Nm" / "N.Nh" (legacy `fmtDur`). */
function fmtDur(s: number | null | undefined): string {
  if (s === null || s === undefined) return "-";
  if (s < 60) return Math.round(s) + "s";
  if (s < 3600) return (s / 60).toFixed(1) + "m";
  return (s / 3600).toFixed(1) + "h";
}

// ── Types ────────────────────────────────────────────────────────────────────

interface StatsRow {
  repo: string;
  workflow_name: string;
  count: number;
  success_rate: number;
  p50_duration: number | null;
  p95_duration: number | null;
  p50_queued: number | null;
  p95_queued: number | null;
}

interface StatsData {
  rows: StatsRow[];
  window_days?: number;
}

interface TimeseriesPoint {
  t: string;
  p50_duration: number | null;
  p95_duration: number | null;
}

// ════════════════════════ STATS TAB (workflow durations) ════════════════════

export function StatsTab(): React.ReactElement {
  const [data, setData] = useState<StatsData>({ rows: [], window_days: 14 });
  const [loading, setLoading] = useState(false);
  const [timeseries, setTimeseries] = useState<{ series: TimeseriesPoint[] }>({ series: [] });
  const [groupBy, setGroupBy] = useState("workflow");
  const [days, setDays] = useState(14);
  const [selected, setSelected] = useState<StatsRow | null>(null);

  function refresh(): void {
    setLoading(true);
    const wfParams = new URLSearchParams({ days: String(days), group_by: groupBy });
    legacyFetch("/api/stats/workflows?" + wfParams)
      .then((r) => r.json())
      .then((d) => {
        setData(d || { rows: [] });
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }
  function refreshTimeseries(row: StatsRow | null): void {
    if (!row) {
      setTimeseries({ series: [] });
      return;
    }
    const tsParamObj: Record<string, string> = {
      days: String(Math.max(days, 30)),
      bucket_hours: "24",
    };
    if (groupBy === "workflow") {
      tsParamObj.repo = row.repo;
      tsParamObj.workflow_name = row.workflow_name;
    } else {
      tsParamObj.repo = row.repo;
    }
    legacyFetch("/api/stats/workflows/timeseries?" + new URLSearchParams(tsParamObj))
      .then((r) => r.json())
      .then((d) => {
        setTimeseries(d || { series: [] });
      });
  }
  function collectNow(): void {
    setLoading(true);
    legacyFetch("/api/stats/workflows/collect", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((r) => r.json())
      .then(() => {
        refresh();
      });
  }
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, groupBy]);
  useEffect(() => {
    refreshTimeseries(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const rows = data.rows || [];
  const totalRuns = rows.reduce((a, r) => a + (r.count || 0), 0);
  const avgP50 = rows.length
    ? rows.reduce((a, r) => a + (r.p50_duration || 0), 0) / rows.length
    : 0;

  function drawSparkline(series: TimeseriesPoint[], height: number): React.ReactElement {
    if (!series || series.length < 2) {
      return (
        <div style={{ color: "var(--text-muted)", fontSize: 12, padding: 20, textAlign: "center" }}>
          Not enough data yet — collector runs every 10 minutes.
        </div>
      );
    }
    const w = 800;
    const h0 = height || 180;
    const maxD = Math.max.apply(null, series.map((s) => s.p95_duration || 0)) || 1;
    function xy(i: number, v: number | null): [number, number] {
      return [(i / (series.length - 1)) * w, h0 - ((v || 0) / maxD) * (h0 - 20) - 5];
    }
    const p50 = series.map((s, i) => xy(i, s.p50_duration).join(",")).join(" ");
    const p95 = series.map((s, i) => xy(i, s.p95_duration).join(",")).join(" ");
    return (
      <div style={{ position: "relative" }}>
        <svg
          viewBox={"0 0 " + w + " " + h0}
          style={{ width: "100%", height: h0, background: "rgba(63,185,80,0.02)", borderRadius: 8 }}
        >
          <polyline points={p95} fill="none" stroke="var(--accent-red)" strokeWidth={2} opacity={0.7} />
          <polyline points={p50} fill="none" stroke="var(--accent-green)" strokeWidth={2} />
          {series.map((s, i) => {
            const c = xy(i, s.p50_duration);
            return <circle key={i} cx={c[0]} cy={c[1]} r={2} fill="var(--accent-green)" />;
          })}
        </svg>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 11,
            color: "var(--text-muted)",
            marginTop: 4,
          }}
        >
          <span>{series[0].t.slice(0, 10)}</span>
          <span>
            <span style={{ color: "var(--accent-green)" }}>p50 </span>
            <span style={{ color: "var(--accent-red)", marginLeft: 8 }}>p95</span>
          </span>
          <span>{series[series.length - 1].t.slice(0, 10)}</span>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="stat-row">
        <Stat
          label="Tracked workflows"
          value={rows.length}
          sub={(data.window_days || days) + "-day window"}
        />
        <Stat label="Completed runs" value={totalRuns} />
        <Stat label="Mean P50 duration" value={fmtDur(avgP50)} />
        <Stat label="Auto-refresh" value="on demand" sub="collector polls every 10m" />
      </div>
      <div
        style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}
      >
        <label style={{ fontSize: 13, color: "var(--text-secondary)" }}>Group by:</label>
        <select
          value={groupBy}
          onChange={(e) => {
            setGroupBy(e.target.value);
            setSelected(null);
          }}
          style={{
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "4px 8px",
          }}
        >
          <option value="workflow">Workflow (repo + name)</option>
          <option value="repo">Repository</option>
        </select>
        <label style={{ fontSize: 13, color: "var(--text-secondary)", marginLeft: 12 }}>
          Window:
        </label>
        <select
          value={days}
          onChange={(e) => {
            setDays(parseInt(e.target.value));
            setSelected(null);
          }}
          style={{
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "4px 8px",
          }}
        >
          {[7, 14, 30, 60, 90].map((d) => (
            <option key={d} value={d}>
              {d + " days"}
            </option>
          ))}
        </select>
        <button className="btn" onClick={refresh} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </button>
        <button
          className="btn"
          onClick={collectNow}
          title="Force collector to run now (otherwise runs every 10 min)"
        >
          Collect now
        </button>
      </div>
      {selected ? (
        <div className="card" style={{ marginBottom: 12 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <div>
              <div style={{ fontWeight: 600, fontSize: 15 }}>
                {groupBy === "workflow"
                  ? selected.repo + " · " + selected.workflow_name
                  : selected.repo}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                P50 duration over time — hover the chart for bucketed detail
              </div>
            </div>
            <button
              className="btn"
              onClick={() => {
                setSelected(null);
              }}
            >
              Close
            </button>
          </div>
          {drawSparkline(timeseries.series, 200)}
        </div>
      ) : null}
      {loading && rows.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
          No data yet. Click ‘Collect now’ to pull recent runs from GitHub, then wait ~1 min for the
          first pass.
        </div>
      ) : (
        <div className="card">
          <table className="run-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th>Repo</th>
                {groupBy === "workflow" ? <th>Workflow</th> : null}
                <th>Runs</th>
                <th>Success %</th>
                <th>P50 dur</th>
                <th>P95 dur</th>
                <th>P50 queued</th>
                <th>P95 queued</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const isSel =
                  selected && selected.repo === r.repo && selected.workflow_name === r.workflow_name;
                return (
                  <tr key={i} style={isSel ? { background: "rgba(88,166,255,0.06)" } : {}}>
                    <td>{r.repo}</td>
                    {groupBy === "workflow" ? (
                      <td
                        style={{
                          maxWidth: 300,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={r.workflow_name}
                      >
                        {r.workflow_name}
                      </td>
                    ) : null}
                    <td>{r.count}</td>
                    <td
                      style={{
                        color:
                          r.success_rate >= 90
                            ? "var(--accent-green)"
                            : r.success_rate >= 70
                              ? "var(--accent-yellow)"
                              : "var(--accent-red)",
                      }}
                    >
                      {r.success_rate + "%"}
                    </td>
                    <td>{fmtDur(r.p50_duration)}</td>
                    <td>{fmtDur(r.p95_duration)}</td>
                    <td>{fmtDur(r.p50_queued)}</td>
                    <td>{fmtDur(r.p95_queued)}</td>
                    <td>
                      <button
                        className="btn"
                        onClick={() => {
                          setSelected(r);
                        }}
                      >
                        Trend
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ════════════════════════ PERFORMANCE TAB (web vitals) ══════════════════════

interface VitalMetric {
  p50: number | null;
  p75: number | null;
  p95: number | null;
  count: number;
}

interface PerformanceData {
  routes: Record<string, Record<string, VitalMetric>>;
  sample_rate?: number;
}

export function PerformanceTab(): React.ReactElement {
  const [data, setData] = useState<PerformanceData>({ routes: {} });
  const [loading, setLoading] = useState(false);

  function refresh(): void {
    setLoading(true);
    legacyFetch("/api/metrics/web-vitals")
      .then((r) => r.json())
      .then((d) => {
        setData(d || { routes: {} });
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    refresh();
  }, []);

  const routes = Object.keys(data.routes || {});
  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 16, margin: 0 }}>Web Vitals</h2>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {"Sample rate: " + ((data.sample_rate || 0) * 100).toFixed(0) + "%"}
        </span>
        <button className="btn" onClick={refresh} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </button>
      </div>
      {routes.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
          No web-vitals data yet.
        </div>
      ) : (
        routes.map((route) => {
          const metrics = data.routes[route];
          return (
            <div key={route} className="card" style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>{route}</div>
              <table className="run-table" style={{ width: "100%" }}>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>P50</th>
                    <th>P75</th>
                    <th>P95</th>
                    <th>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(metrics).map((name) => {
                    const m = metrics[name];
                    return (
                      <tr key={name}>
                        <td>{name}</td>
                        <td>{m.p50 != null ? m.p50.toFixed(1) : "-"}</td>
                        <td>{m.p75 != null ? m.p75.toFixed(1) : "-"}</td>
                        <td>{m.p95 != null ? m.p95.toFixed(1) : "-"}</td>
                        <td>{m.count || 0}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })
      )}
    </div>
  );
}

// ════════════════════════ ANALYSIS OUTCOMES TAB ═════════════════════════════

interface OutcomeMachine {
  machine_name: string;
  count: number;
  success_rate: number;
  avg_duration_seconds: number | null;
}

interface OutcomeWorkflow {
  key: string;
  repo: string;
  workflow_name: string;
  count: number;
  failure?: number;
  avg_duration_seconds: number | null;
}

interface OutcomeMatrixRow {
  key: string;
  repo: string;
  workflow_name: string;
  machine_name: string;
  count: number;
  success_rate: number;
  avg_duration_seconds: number | null;
}

interface OutcomesData {
  sample_size?: number;
  success_rate?: number;
  workflows: OutcomeWorkflow[];
  machines: OutcomeMachine[];
  matrix: OutcomeMatrixRow[];
  failure_reasons?: Record<string, unknown>;
}

const EMPTY_OUTCOMES: OutcomesData = {
  sample_size: 0,
  success_rate: 0,
  workflows: [],
  machines: [],
  matrix: [],
  failure_reasons: {},
};

export function AnalysisOutcomesTab(): React.ReactElement {
  const [data, setData] = useState<OutcomesData>(EMPTY_OUTCOMES);
  const [loading, setLoading] = useState(false);

  function refresh(): void {
    setLoading(true);
    legacyFetch("/api/analysis/workflow-machines?per_page=100")
      .then((r) => r.json())
      .then((d) => {
        setData(d || EMPTY_OUTCOMES);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }
  useEffect(() => {
    refresh();
  }, []);

  const machines = data.machines || [];
  const workflows = data.workflows || [];
  const matrix = data.matrix || [];
  const failureReasons = Object.keys(data.failure_reasons || {});
  const slowest = matrix.filter((row) => row.avg_duration_seconds != null).slice(0, 10);
  const weakest = workflows.slice(0, 10);
  return (
    <div>
      <div className="stat-row">
        <Stat label="Recent enriched runs" value={data.sample_size || 0} sub="job placement sample" />
        <Stat
          label="Success rate"
          value={(data.success_rate || 0) + "%"}
          sub="recent completed sample"
        />
        <Stat label="Machines seen" value={machines.length} />
        <Stat
          label="Failure reasons"
          value={failureReasons.length || 0}
          sub={failureReasons.slice(0, 3).join(", ") || "none"}
        />
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          Recent outcomes by workflow and runner machine. Use this to separate slow workflows from
          slow hosts.
        </div>
        <button className="btn" onClick={refresh} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </button>
      </div>
      <div className="grid-2">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Machine Health From Jobs</h3>
          {machines.length === 0 ? (
            <div style={{ color: "var(--text-muted)", padding: 20 }}>
              No machine placement data yet.
            </div>
          ) : (
            <table className="run-table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>Machine</th>
                  <th>Runs</th>
                  <th>Success</th>
                  <th>Avg duration</th>
                </tr>
              </thead>
              <tbody>
                {machines.map((row) => (
                  <tr key={row.machine_name}>
                    <td>{row.machine_name}</td>
                    <td>{row.count}</td>
                    <td>{row.success_rate + "%"}</td>
                    <td>{fmtDur(row.avg_duration_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Highest Attention Workflows</h3>
          {weakest.length === 0 ? (
            <div style={{ color: "var(--text-muted)", padding: 20 }}>
              No workflow outcome data yet.
            </div>
          ) : (
            <table className="run-table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Runs</th>
                  <th>Failures</th>
                  <th>Avg duration</th>
                </tr>
              </thead>
              <tbody>
                {weakest.map((row) => (
                  <tr key={row.key}>
                    <td title={row.repo + " / " + row.workflow_name}>{row.workflow_name}</td>
                    <td>{row.count}</td>
                    <td>{row.failure || 0}</td>
                    <td>{fmtDur(row.avg_duration_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <h3 style={{ marginTop: 0 }}>Workflow x Machine Runtime</h3>
        {slowest.length === 0 ? (
          <div style={{ color: "var(--text-muted)", padding: 20 }}>
            No completed workflow-machine pairs in the current sample.
          </div>
        ) : (
          <table className="run-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th>Repo</th>
                <th>Workflow</th>
                <th>Machine</th>
                <th>Runs</th>
                <th>Success</th>
                <th>Avg duration</th>
              </tr>
            </thead>
            <tbody>
              {slowest.map((row) => (
                <tr key={row.key}>
                  <td>{row.repo}</td>
                  <td title={row.workflow_name}>{row.workflow_name}</td>
                  <td>{row.machine_name}</td>
                  <td>{row.count}</td>
                  <td>{row.success_rate + "%"}</td>
                  <td>{fmtDur(row.avg_duration_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ════════════════════════ REPORTS TAB ═══════════════════════════════════════

export interface ReportSummary {
  date: string;
  size_kb: number;
  has_chart?: boolean;
}

interface ReportMetric {
  value?: string | number;
  delta?: string;
}

export interface ReportsTabProps {
  reports: ReportSummary[];
  loading?: boolean;
}

export function ReportsTab({ reports, loading }: ReportsTabProps): React.ReactElement {
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<Record<string, ReportMetric>>({});
  const [rl, setRl] = useState(false);

  function loadReport(date: string): void {
    setSelected(date);
    setRl(true);
    legacyFetch("/api/reports/" + date)
      .then((r) => r.json())
      .then((data) => {
        setContent(data.content || "");
        setMetrics(data.metrics || {});
        setRl(false);
      })
      .catch(() => {
        setContent("Failed to load report.");
        setRl(false);
      });
  }

  // Auto-load latest on mount.
  useEffect(() => {
    if (reports.length > 0 && !selected) {
      loadReport(reports[0].date);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reports.length]);

  function renderMarkdown(md: string): { __html: string } {
    if (typeof marked !== "undefined" && marked.parse) {
      const raw = marked.parse(md) as string;
      // Sanitize via DOMPurify to prevent XSS (issue #7).
      const clean = typeof DOMPurify !== "undefined" ? DOMPurify.sanitize(raw) : raw;
      return { __html: clean };
    }
    // Fallback: just show raw text (HTML-escaped, inherently safe).
    return { __html: "<pre>" + md.replace(/</g, "&lt;") + "</pre>" };
  }

  const prsMerged = metrics["PRs Merged (24h)"] || {};
  const issuesOpen = metrics["Issues Currently Open"] || {};
  const score =
    metrics["Fleet Average Score (20 repos)"] || metrics["Fleet Average Score"] || {};
  const ciIssue = metrics["PRs Merged with Failing CI"] || {};

  return (
    <div>
      {reports.length > 0 ? (
        <div className="stat-row">
          <Stat label="Latest Report" value={reports[0].date} sub={reports[0].size_kb + " KB"} />
          <Stat
            label="PRs Merged"
            value={prsMerged.value || "-"}
            color="var(--accent-blue)"
            sub={prsMerged.delta || ""}
          />
          <Stat
            label="Issues Open"
            value={issuesOpen.value || "-"}
            color="var(--accent-orange)"
            sub={issuesOpen.delta || ""}
          />
          <Stat
            label="Fleet Score"
            value={score.value || "-"}
            color="var(--accent-green)"
            sub={score.delta || ""}
          />
          {ciIssue.value ? (
            <Stat
              label="Failing CI Merges"
              value={ciIssue.value}
              color="var(--accent-red)"
              sub={ciIssue.delta || ""}
            />
          ) : null}
        </div>
      ) : null}
      <div className="reports-shell">
        <div className="reports-sidebar">
          <div className="section">
            <div className="reports-section-title">
              Reports {loading ? <span className="spinner" /> : null}
            </div>
            <ul className="report-list">
              {reports.map((r) => (
                <li
                  key={r.date}
                  className={"report-item" + (selected === r.date ? " active" : "")}
                  onClick={() => {
                    loadReport(r.date);
                  }}
                >
                  <div>
                    <div className="report-date">{r.date}</div>
                    <div className="report-meta">
                      {r.size_kb + " KB"}
                      {r.has_chart ? " · 📈" : ""}
                    </div>
                  </div>
                </li>
              ))}
              {reports.length === 0 ? (
                <li className="report-list-empty">
                  No reports found
                </li>
              ) : null}
            </ul>
          </div>
        </div>
        <div className="reports-reader">
          {selected ? (
            <div>
              <div className="reports-reader__header">
                <span
                  className="section-badge report-selected-badge"
                >
                  {selected}
                </span>
                <a
                  className="report-open-raw"
                  href={"/api/reports/" + selected}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open raw
                </a>
              </div>
              {reports.filter((r) => r.date === selected && r.has_chart).length > 0 ? (
                <div className="report-chart">
                  <img
                    src={"/api/reports/" + selected + "/chart"}
                    alt="Assessment Scores"
                    className="report-chart__image"
                  />
                </div>
              ) : null}
              {rl ? (
                <div className="reports-loading">
                  <span className="spinner" />
                </div>
              ) : (
                <div
                  className="report-content"
                  dangerouslySetInnerHTML={renderMarkdown(content || "")}
                />
              )}
            </div>
          ) : (
            <div className="reports-empty">
              Select a report from the list
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ════════════════════════ ANALYSIS TAB (orchestrator) ════════════════════════
// The sub-tab strip + leaf-panel fan-out, extracted from the legacy `App.tsx`
// monolith (decomposition #836, pass 12). The four leaf panels above render
// inline; the "History" sub-tab still delegates to the legacy `HistoryTab`.

const h = React.createElement;

export interface AnalysisTabProps {
  activeTab?: string;
  runs?: unknown[];
  runners?: unknown[];
  reports?: ReportSummary[];
  reportsLoading?: boolean;
}

export function AnalysisTab(p: AnalysisTabProps): React.ReactElement {
  const legacyKey =
    isAnalysisTabKey(p.activeTab) && p.activeTab !== "analysis"
      ? p.activeTab
      : null;
  const initial =
    legacyKey || localStorage.getItem("analysis-subtab") || "outcomes";
  const ss = useState(initial);
  const subTab = ss[0],
    setSubTab = ss[1];
  function changeSubTab(key: string) {
    setSubTab(key);
    try {
      localStorage.setItem("analysis-subtab", key);
    } catch (e) {
      /* localStorage may be unavailable; ignore (legacy 1:1). */
    }
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
            : h(ReportsTab, { reports: p.reports || [], loading: p.reportsLoading }),
  );
}
