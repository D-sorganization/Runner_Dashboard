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
import { Badge, type BadgeTone } from "../primitives/Badge";
import { EmptyState } from "../primitives/EmptyState";
import { SkeletonCard } from "../primitives/Skeleton";
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

function conclusionTone(conclusion?: string | null): BadgeTone {
  if (conclusion === "success") return "success";
  if (conclusion === "failure") return "danger";
  if (conclusion === "cancelled") return "neutral";
  return "warning";
}

function StatusCell({ wf }: { wf: FlatWorkflow }): React.ReactElement {
  if (!wf.enabled) {
    return <Badge tone="neutral" size="sm">disabled</Badge>;
  }
  const lr = wf.latest_run;
  if (!lr) {
    return <Badge tone="neutral" size="sm">no runs</Badge>;
  }
  if (lr.conclusion) {
    return <Badge tone={conclusionTone(lr.conclusion)} size="sm">{lr.conclusion}</Badge>;
  }
  return <Badge tone="info" size="sm">{lr.status || "running"}</Badge>;
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "blue" | "purple" | "green" | "orange" | "red";
}): React.ReactElement {
  return (
    <div className="stat">
      <div className={`stat-value scheduled-jobs__stat-value scheduled-jobs__stat-value--${tone}`}>
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
        <Stat label="Scheduled Workflows" value={totalScheduled} tone="blue" />
        <Stat
          label="Repos w/ Schedules"
          value={reposWithSchedules.length}
          tone="purple"
        />
        {julesCount > 0 ? (
          <Stat label="Jules Schedules" value={julesCount} tone="green" />
        ) : null}
        {disabledCount > 0 ? (
          <Stat label="Disabled" value={disabledCount} tone="orange" />
        ) : null}
        {dryRunSteps > 0 ? (
          <Stat label="Dry-Run Actions" value={dryRunSteps} tone="red" />
        ) : null}
      </div>

      <div className="section-header">
        <span className="section-title">
          <ClockGlyph size={14} /> Scheduled Workflows
          {allWorkflows.length > 0 ? (
            <span className="section-badge scheduled-jobs__count-badge">
              {filtered.length}
            </span>
          ) : null}
        </span>
        <div className="scheduled-jobs__toolbar">
          <input
            type="text"
            aria-label="Filter scheduled workflows by name or repo"
            placeholder={"Filter by name or repo…"}
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="scheduled-jobs__filter"
          />
          <select
            aria-label="Filter scheduled workflows by repository"
            value={filterRepo}
            onChange={(e) => setFilterRepo(e.target.value)}
            className="scheduled-jobs__repo-select"
          >
            <option value="all">All repos</option>
            {reposWithSchedules.map((r) => (
              <option key={r.repository} value={r.repository}>
                {r.repository}
              </option>
            ))}
          </select>
          {loading ? (
            <span className="scheduled-jobs__meta">Loading…</span>
          ) : null}
          {data.generated_at ? (
            <span className="scheduled-jobs__meta">
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
        <div className="scheduled-jobs__loading" role="status" aria-label="Loading scheduled workflows">
          <SkeletonCard lines={4} />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          title={
            allWorkflows.length === 0
              ? "No scheduled workflows found."
              : "No workflows match the current filter."
          }
          description={
            allWorkflows.length === 0
              ? "Scheduled workflow inventory will appear here after the next successful scan."
              : "Adjust the name or repository filter to broaden the result set."
          }
          data-testid="scheduled-jobs-empty"
        />
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
                        className="scheduled-jobs__jules-marker"
                        title="Jules workflow"
                      >
                        {"◆"}
                      </span>
                    ) : null}
                    {wf.workflow_name}
                  </td>
                  <td className="scheduled-jobs__muted-cell">{wf.repo}</td>
                  <td className="scheduled-jobs__code-cell">
                    {wf.cron_expressions && wf.cron_expressions.length > 0 ? (
                      wf.cron_expressions.join(", ")
                    ) : (
                      <span className="scheduled-jobs__muted-cell">{"—"}</span>
                    )}
                  </td>
                  <td>
                    <StatusCell wf={wf} />
                  </td>
                  <td className="scheduled-jobs__muted-cell">
                    {lr ? timeAgo(lr.created_at) : "—"}
                  </td>
                  <td>
                    {lr && lr.html_url ? (
                      <a
                        href={lr.html_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="scheduled-jobs__run-link"
                      >
                        {lr.conclusion || lr.status || "in progress"}
                      </a>
                    ) : (
                      <span className="scheduled-jobs__muted-cell">{"—"}</span>
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
          <div className="section-header scheduled-jobs__dry-run-header">
            <span className="section-title">
              Dry-Run Plan
              <span className="section-badge scheduled-jobs__dry-run-badge">
                {dryRunSteps}
              </span>
            </span>
            <span className="scheduled-jobs__meta">
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
                    <code className="scheduled-jobs__action-code">{step.action}</code>
                  </td>
                  <td className="scheduled-jobs__compact-cell">{step.workflow_name}</td>
                  <td className="scheduled-jobs__muted-cell">{step.repository}</td>
                  <td className="scheduled-jobs__reason-cell">
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
