import React, { useCallback, useEffect, useMemo, useState } from "react";
import { legacyFetch } from "../../lib/api";

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

interface FlatWorkflow extends ScheduledWorkflow {
  repo: string;
}

export interface OperationsScheduledWorkflowsSectionProps {
  initialData?: ScheduledWorkflowsData;
  onDataChange?: (data: ScheduledWorkflowsData) => void;
}

export function OperationsScheduledWorkflowsSection({
  initialData,
  onDataChange,
}: OperationsScheduledWorkflowsSectionProps): React.ReactElement {
  const [data, setData] = useState<ScheduledWorkflowsData>(initialData ?? {});
  const [loading, setLoading] = useState(initialData === undefined);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    legacyFetch("/api/scheduled-workflows", { signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<ScheduledWorkflowsData>;
      })
      .then((payload: ScheduledWorkflowsData | null) => {
        if (payload) {
          setData(payload);
          onDataChange?.(payload);
        }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Failed to load scheduled workflows");
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, [onDataChange]);

  useEffect(() => {
    if (initialData === undefined) {
      const controller = new AbortController();
      load(controller.signal);
      return () => controller.abort();
    }
  }, [load, initialData]);

  const flatWorkflows: FlatWorkflow[] = useMemo(() => {
    const list: FlatWorkflow[] = [];
    for (const repo of data.repositories || []) {
      for (const wf of repo.workflows || []) {
        list.push({ ...wf, repo: repo.repository });
      }
    }
    return list;
  }, [data.repositories]);

  const filteredWorkflows = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return flatWorkflows;
    return flatWorkflows.filter(
      (wf) =>
        wf.workflow_name.toLowerCase().includes(term) ||
        wf.repo.toLowerCase().includes(term) ||
        wf.workflow_path.toLowerCase().includes(term),
    );
  }, [flatWorkflows, search]);

  const renderStatus = (wf: FlatWorkflow) => {
    if (!wf.enabled) {
      return (
        <span
          style={{
            padding: "0.15rem 0.5rem",
            borderRadius: "4px",
            fontSize: "0.75rem",
            fontWeight: 500,
            background: "var(--badge-neutral-bg, rgba(110,118,129,0.2))",
            color: "var(--badge-neutral-text, #8b949e)",
          }}
        >
          disabled
        </span>
      );
    }
    const lr = wf.latest_run;
    if (!lr) {
      return (
        <span
          style={{
            padding: "0.15rem 0.5rem",
            borderRadius: "4px",
            fontSize: "0.75rem",
            fontWeight: 500,
            background: "var(--badge-neutral-bg, rgba(110,118,129,0.2))",
            color: "var(--badge-neutral-text, #8b949e)",
          }}
        >
          no runs
        </span>
      );
    }

    const conclusion = lr.conclusion;
    const toneBg =
      conclusion === "success"
        ? "var(--badge-success-bg, rgba(46,160,67,0.15))"
        : conclusion === "failure"
          ? "var(--badge-danger-bg, rgba(248,81,73,0.15))"
          : "var(--badge-warning-bg, rgba(210,153,34,0.15))";
    const toneText =
      conclusion === "success"
        ? "var(--badge-success-text, #3fb950)"
        : conclusion === "failure"
          ? "var(--badge-danger-text, #f85149)"
          : "var(--badge-warning-text, #d29922)";

    const badge = (
      <span
        style={{
          padding: "0.15rem 0.5rem",
          borderRadius: "4px",
          fontSize: "0.75rem",
          fontWeight: 600,
          background: toneBg,
          color: toneText,
        }}
      >
        {conclusion || lr.status || "running"}
      </span>
    );

    if (lr.html_url) {
      return (
        <a
          href={lr.html_url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ textDecoration: "none" }}
        >
          {badge}
        </a>
      );
    }
    return badge;
  };

  return (
    <section
      id="scheduled-workflows"
      aria-labelledby="heading-scheduled-workflows"
      style={{
        padding: "1.25rem",
        marginBottom: "1.5rem",
        borderRadius: "8px",
        background: "var(--bg-secondary, #161b22)",
        border: "1px solid var(--border-color, #30363d)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "1rem",
          marginBottom: "1rem",
        }}
      >
        <div>
          <h2
            id="heading-scheduled-workflows"
            style={{
              fontSize: "1.25rem",
              fontWeight: 600,
              margin: "0 0 0.25rem 0",
              color: "var(--text-primary, #c9d1d9)",
            }}
          >
            Scheduled workflows
          </h2>
          <p
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted, #8b949e)",
              margin: 0,
            }}
          >
            Organization-wide scheduled cron workflows, latest run outcomes, and execution plans
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => load()}
            disabled={loading}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-secondary, #8b949e)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            marginBottom: "1rem",
            borderRadius: "6px",
            background: "var(--bg-danger-subtle, rgba(248,81,73,0.1))",
            border: "1px solid var(--border-danger, #f85149)",
            color: "var(--text-danger, #f85149)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>Failed to load scheduled workflows: {error}</span>
          <button
            type="button"
            onClick={() => load()}
            style={{
              padding: "0.25rem 0.6rem",
              fontSize: "0.75rem",
              fontWeight: 600,
              borderRadius: "4px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-primary, #c9d1d9)",
              border: "1px solid var(--border-color, #30363d)",
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* KPI stats */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "0.75rem",
          marginBottom: "1rem",
        }}
      >
        <div
          style={{
            padding: "0.625rem 0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>WORKFLOWS</div>
          <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
            {data.scheduled_workflow_count ?? flatWorkflows.length} workflows
          </div>
        </div>
        <div
          style={{
            padding: "0.625rem 0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>REPOSITORIES</div>
          <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
            {data.repositories?.length ?? 0} repos
          </div>
        </div>
      </div>

      {/* Search Filter */}
      <div style={{ marginBottom: "1rem" }}>
        <input
          type="search"
          placeholder="Filter workflows by name or repository..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            width: "100%",
            boxSizing: "border-box",
            padding: "0.5rem 0.75rem",
            fontSize: "0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-color, #30363d)",
            color: "var(--text-primary, #c9d1d9)",
          }}
        />
      </div>

      {/* Workflows table */}
      <div
        style={{
          border: "1px solid var(--border-color, #30363d)",
          borderRadius: "6px",
          overflow: "hidden",
          background: "var(--bg-tertiary, #21262d)",
          marginBottom: "1rem",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-color, #30363d)", textAlign: "left" }}>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>REPOSITORY</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>WORKFLOW</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>SCHEDULE CRON</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>LATEST STATUS</th>
            </tr>
          </thead>
          <tbody>
            {filteredWorkflows.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: "1.5rem", textAlign: "center", color: "var(--text-muted, #8b949e)" }}>
                  {search ? "No matching workflows found." : "No scheduled workflows discovered."}
                </td>
              </tr>
            ) : (
              filteredWorkflows.map((wf, idx) => (
                <tr
                  key={`${wf.repo}-${wf.workflow_name}-${idx}`}
                  style={{
                    borderBottom: idx < filteredWorkflows.length - 1 ? "1px solid var(--border-subtle, #30363d)" : "none",
                  }}
                >
                  <td style={{ padding: "0.625rem 0.875rem", fontWeight: 500, color: "var(--text-primary, #c9d1d9)" }}>
                    {wf.repo}
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem", color: "var(--text-primary, #c9d1d9)" }}>
                    <code>{wf.workflow_name}</code>
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem", color: "var(--text-secondary, #8b949e)" }}>
                    {(wf.cron_expressions || []).join(", ") || "—"}
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem" }}>{renderStatus(wf)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Dry Run Plan */}
      {data.dry_run_plan?.steps && data.dry_run_plan.steps.length > 0 && (
        <div
          style={{
            padding: "0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontSize: "0.8125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)", marginBottom: "0.5rem" }}>
            Execution Plan (Dry Run)
          </div>
          <ul style={{ margin: 0, paddingLeft: "1.25rem", fontSize: "0.8125rem", color: "var(--text-secondary, #8b949e)" }}>
            {data.dry_run_plan.steps.map((step, idx) => (
              <li key={idx} style={{ marginBottom: "0.25rem" }}>
                <strong style={{ color: "var(--text-primary, #c9d1d9)" }}>{step.action}</strong> {step.repository}/{step.workflow_name}: {step.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default OperationsScheduledWorkflowsSection;
