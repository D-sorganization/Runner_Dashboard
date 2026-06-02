/**
 * ScheduledJobs.tsx — the "Schedules" tab, extracted from the legacy `App.tsx`
 * monolith as part of the decomposition epic (#836, pass 2).
 *
 * Lists every scheduled (cron) workflow across the organization, with
 * name/repo filtering, headline stats, latest-run status, and the read-only
 * dry-run plan when the backend provides one. Self-fetching: it owns its own
 * `/api/scheduled-workflows` poll (every 5 min, matching the legacy interval)
 * and a manual Refresh button, so the legacy App no longer has to thread this
 * state through props.
 *
 * Loading/empty states and a11y semantics mirror the original legacy render.
 */
import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import { ClockGlyph, RefreshGlyph } from "./decompIcons";

// ── Types ──────────────────────────────────────────────────────────────────

interface LatestRun {
  status?: string | null;
  conclusion?: string | null;
  created_at?: string | null;
  html_url?: string | null;
}

interface ScheduledWorkflow {
  workflow_name: string;
  workflow_path: string;
  scheduled?: boolean;
  enabled?: boolean;
  cron_expressions?: string[];
  latest_run?: LatestRun | null;
}

interface ScheduledRepo {
  repository: string;
  scheduled_workflow_count?: number;
  workflows?: ScheduledWorkflow[];
}

interface DryRunStep {
  action: string;
  workflow_name: string;
  repository: string;
  reason: string;
}

interface DryRunPlan {
  steps?: DryRunStep[];
}

export interface ScheduledWorkflowsData {
  repositories?: ScheduledRepo[];
  scheduled_workflow_count?: number;
  generated_at?: string | null;
  dry_run_plan?: DryRunPlan | null;
}

/** A workflow enriched with the repository it belongs to (flattened). */
interface FlatWorkflow extends ScheduledWorkflow {
  repo: string;
}

// ── Helpers (copied from legacy App to keep the page self-contained) ─────────

function timeAgo(d?: string | null): string {
  if (!d) return "";
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function conclusionColor(c?: string | null): string {
  if (c === "success") return "var(--accent-green)";
  if (c === "failure") return "var(--accent-red)";
  if (c === "cancelled") return "var(--fg-muted)";
  return "var(--accent-orange)";
}

const pillBase: React.CSSProperties = {
  fontSize: 11,
  padding: "2px 6px",
  borderRadius: 4,
};

function StatusCell({ wf }: { wf: FlatWorkflow }): React.ReactElement {
  if (!wf.enabled) {
    return (
      <span style={{ ...pillBase, background: "rgba(139,148,158,0.15)", color: "var(--fg-muted)" }}>
        disabled
      </span>
    );
  }
  const lr = wf.latest_run;
  if (!lr) {
    return (
      <span style={{ ...pillBase, background: "rgba(139,148,158,0.1)", color: "var(--fg-muted)" }}>
        no runs
      </span>
    );
  }
  if (lr.conclusion) {
    const cc = conclusionColor(lr.conclusion);
    return (
      <span style={{ ...pillBase, background: cc + "22", color: cc }}>{lr.conclusion}</span>
    );
  }
  return (
    <span style={{ ...pillBase, background: "rgba(88,166,255,0.15)", color: "var(--accent-blue)" }}>
      {lr.status || "running"}
    </span>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}): React.ReactElement {
  return (
    <div className="stat">
      <div className="stat-value" style={{ color }}>
        {value}
      </div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function ScheduledJobs(): React.ReactElement {
  const [data, setData] = useState<ScheduledWorkflowsData>({});
  const [loading, setLoading] = useState(false);
  const [filterText, setFilterText] = useState("");
  const [filterRepo, setFilterRepo] = useState("all");

  const fetchScheduledJobs = useCallback(() => {
    setLoading(true);
    legacyFetch("/api/scheduled-workflows")
      .then((r) => r.json())
      .then((d: ScheduledWorkflowsData | null) => {
        if (d) setData(d);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    fetchScheduledJobs();
    const t = setInterval(fetchScheduledJobs, 300000);
    return () => clearInterval(t);
  }, [fetchScheduledJobs]);

  const repos = data.repositories ?? [];
  const totalScheduled = data.scheduled_workflow_count ?? 0;
  const dryRunSteps = data.dry_run_plan ? (data.dry_run_plan.steps ?? []).length : 0;

  const allWorkflows: FlatWorkflow[] = [];
  repos.forEach((repo) => {
    (repo.workflows ?? []).forEach((wf) => {
      if (!wf.scheduled) return;
      allWorkflows.push({ ...wf, repo: repo.repository });
    });
  });

  const filtered = allWorkflows.filter((wf) => {
    if (filterRepo !== "all" && wf.repo !== filterRepo) return false;
    if (filterText) {
      const q = filterText.toLowerCase();
      return (
        wf.workflow_name.toLowerCase().indexOf(q) !== -1 ||
        wf.repo.toLowerCase().indexOf(q) !== -1
      );
    }
    return true;
  });

  const julesCount = allWorkflows.filter(
    (wf) => wf.workflow_name.toLowerCase().indexOf("jules") !== -1,
  ).length;
  const disabledCount = allWorkflows.filter((wf) => !wf.enabled).length;
  const reposWithSchedules = repos.filter((r) => (r.scheduled_workflow_count ?? 0) > 0);

  return (
    <div>
      <div className="stat-row">
        <Stat label="Scheduled Workflows" value={totalScheduled} color="var(--accent-blue)" />
        <Stat
          label="Repos w/ Schedules"
          value={reposWithSchedules.length}
          color="var(--accent-purple)"
        />
        {julesCount > 0 ? (
          <Stat label="Jules Schedules" value={julesCount} color="var(--accent-green)" />
        ) : null}
        {disabledCount > 0 ? (
          <Stat label="Disabled" value={disabledCount} color="var(--accent-orange)" />
        ) : null}
        {dryRunSteps > 0 ? (
          <Stat label="Dry-Run Actions" value={dryRunSteps} color="var(--accent-red)" />
        ) : null}
      </div>

      <div className="section-header">
        <span className="section-title">
          <ClockGlyph size={14} /> Scheduled Workflows
          {allWorkflows.length > 0 ? (
            <span
              className="section-badge"
              style={{
                background: "rgba(88,166,255,0.2)",
                color: "var(--accent-blue)",
                marginLeft: 4,
              }}
            >
              {filtered.length}
            </span>
          ) : null}
        </span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="text"
            aria-label="Filter scheduled workflows by name or repo"
            placeholder={"Filter by name or repo…"}
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            style={{
              fontSize: 12,
              padding: "3px 8px",
              borderRadius: 4,
              border: "1px solid var(--border)",
              background: "var(--bg-card)",
              color: "var(--fg)",
              width: 180,
            }}
          />
          <select
            aria-label="Filter scheduled workflows by repository"
            value={filterRepo}
            onChange={(e) => setFilterRepo(e.target.value)}
            style={{
              fontSize: 12,
              padding: "3px 8px",
              borderRadius: 4,
              border: "1px solid var(--border)",
              background: "var(--bg-card)",
              color: "var(--fg)",
            }}
          >
            <option value="all">All repos</option>
            {reposWithSchedules.map((r) => (
              <option key={r.repository} value={r.repository}>
                {r.repository}
              </option>
            ))}
          </select>
          {loading ? (
            <span style={{ fontSize: 11, color: "var(--fg-muted)" }}>Loading…</span>
          ) : null}
          {data.generated_at ? (
            <span style={{ fontSize: 11, color: "var(--fg-muted)" }}>
              {"Updated " + timeAgo(data.generated_at)}
            </span>
          ) : null}
          <button
            className="btn"
            onClick={fetchScheduledJobs}
            type="button"
            aria-label="Refresh scheduled workflows"
          >
            <RefreshGlyph size={12} />
          </button>
        </div>
      </div>

      {loading && allWorkflows.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px 0", color: "var(--fg-muted)" }}>
          Loading scheduled workflows…
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px 0", color: "var(--fg-muted)" }}>
          {allWorkflows.length === 0
            ? "No scheduled workflows found."
            : "No workflows match the current filter."}
        </div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Workflow</th>
              <th>Repo</th>
              <th>Cron</th>
              <th>Status</th>
              <th>Last Run</th>
              <th>Conclusion</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((wf) => {
              const lr = wf.latest_run;
              const isJules = wf.workflow_name.toLowerCase().indexOf("jules") !== -1;
              return (
                <tr key={wf.repo + "/" + wf.workflow_path}>
                  <td>
                    {isJules ? (
                      <span
                        style={{
                          color: "var(--accent-purple)",
                          fontWeight: 600,
                          marginRight: 4,
                        }}
                        title="Jules workflow"
                      >
                        {"◆"}
                      </span>
                    ) : null}
                    {wf.workflow_name}
                  </td>
                  <td style={{ color: "var(--fg-muted)", fontSize: 12 }}>{wf.repo}</td>
                  <td style={{ fontFamily: "monospace", fontSize: 11, color: "var(--fg-muted)" }}>
                    {wf.cron_expressions && wf.cron_expressions.length > 0 ? (
                      wf.cron_expressions.join(", ")
                    ) : (
                      <span style={{ color: "var(--fg-muted)" }}>{"—"}</span>
                    )}
                  </td>
                  <td>
                    <StatusCell wf={wf} />
                  </td>
                  <td style={{ fontSize: 12, color: "var(--fg-muted)" }}>
                    {lr ? timeAgo(lr.created_at) : "—"}
                  </td>
                  <td>
                    {lr && lr.html_url ? (
                      <a
                        href={lr.html_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "var(--accent-blue)", fontSize: 12 }}
                      >
                        {lr.conclusion || lr.status || "in progress"}
                      </a>
                    ) : (
                      <span style={{ color: "var(--fg-muted)", fontSize: 12 }}>{"—"}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {dryRunSteps > 0 && data.dry_run_plan ? (
        <div>
          <div className="section-header" style={{ marginTop: 16 }}>
            <span className="section-title">
              Dry-Run Plan
              <span
                className="section-badge"
                style={{
                  background: "rgba(255,165,0,0.15)",
                  color: "var(--accent-orange)",
                  marginLeft: 4,
                }}
              >
                {dryRunSteps}
              </span>
            </span>
            <span style={{ fontSize: 11, color: "var(--fg-muted)" }}>
              {"Read-only — no write actions will be performed"}
            </span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Action</th>
                <th>Workflow</th>
                <th>Repo</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {(data.dry_run_plan.steps ?? []).map((step, idx) => (
                <tr key={idx}>
                  <td>
                    <code style={{ fontSize: 11 }}>{step.action}</code>
                  </td>
                  <td style={{ fontSize: 12 }}>{step.workflow_name}</td>
                  <td style={{ fontSize: 12, color: "var(--fg-muted)" }}>{step.repository}</td>
                  <td style={{ fontSize: 11, color: "var(--fg-muted)", maxWidth: 320 }}>
                    {step.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
