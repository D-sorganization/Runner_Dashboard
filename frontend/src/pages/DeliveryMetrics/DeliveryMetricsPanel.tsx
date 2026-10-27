/** Delivery metrics — weekly trends and PR drill-down (#2122). */
import React from "react";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { formatDuration, uniqueField, uniqueRepos } from "./deliveryMetricsApi";
import { useDeliveryMetrics } from "./useDeliveryMetrics";

export function DeliveryMetricsPanel(): React.ReactElement {
  const {
    state,
    repoFilter,
    setRepoFilter,
    roleFilter,
    setRoleFilter,
    agentFilter,
    setAgentFilter,
    modelFilter,
    setModelFilter,
    refetch,
    pullDetail,
    pullLoading,
    pullError,
    selectPull,
    clearPull,
  } = useDeliveryMetrics();

  if (state.kind === "loading") {
    return (
      <p role="status" className="staff-muted" data-testid="delivery-metrics-loading">
        Loading delivery metrics…
      </p>
    );
  }
  if (state.kind === "disabled") {
    return (
      <EmptyState
        title="Delivery metrics are disabled"
        description="Enable DASHBOARD_ENABLE_DELIVERY_METRICS on this node to collect GitHub delivery facts."
        data-testid="delivery-metrics-disabled"
      />
    );
  }
  if (state.kind === "error") {
    return (
      <EmptyState
        variant="error"
        title="Delivery metrics unavailable"
        description={state.message}
        onRetry={refetch}
        data-testid="delivery-metrics-error"
      />
    );
  }

  const { data } = state;
  const repos = uniqueRepos(data.pr_drill_down);
  const roles = uniqueField(data.pr_drill_down, "role");
  const agents = uniqueField(data.pr_drill_down, "agent");
  const models = uniqueField(data.pr_drill_down, "model");
  const rows = data.pr_drill_down.filter((r) => {
    if (repoFilter && r.repo_full !== repoFilter) return false;
    if (roleFilter && r.role !== roleFilter) return false;
    if (agentFilter && r.agent !== agentFilter) return false;
    if (modelFilter && r.model !== modelFilter) return false;
    return true;
  });

  return (
    <div data-testid="delivery-metrics-panel" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="staff-muted" data-testid="delivery-metrics-posture">
        Weekly delivery trends — not leaderboards. Filter by repo, staff role, agent (provider), or model.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <label>
          Repository
          <select
            aria-label="Filter by repository"
            value={repoFilter ?? ""}
            onChange={(e) => setRepoFilter(e.target.value || null)}
            style={{ marginLeft: 8 }}
          >
            <option value="">All tracked repos</option>
            {repos.map((repo) => (
              <option key={repo} value={repo}>{repo}</option>
            ))}
          </select>
        </label>
        <label>
          Role
          <select
            aria-label="Filter by staff role"
            value={roleFilter ?? ""}
            onChange={(e) => setRoleFilter(e.target.value || null)}
            style={{ marginLeft: 8 }}
          >
            <option value="">All roles</option>
            {roles.map((role) => (
              <option key={role} value={role}>{role}</option>
            ))}
          </select>
        </label>
        <label>
          Agent
          <select
            aria-label="Filter by agent provider"
            value={agentFilter ?? ""}
            onChange={(e) => setAgentFilter(e.target.value || null)}
            style={{ marginLeft: 8 }}
          >
            <option value="">All agents</option>
            {agents.map((agent) => (
              <option key={agent} value={agent}>{agent}</option>
            ))}
          </select>
        </label>
        <label>
          Model
          <select
            aria-label="Filter by model"
            value={modelFilter ?? ""}
            onChange={(e) => setModelFilter(e.target.value || null)}
            style={{ marginLeft: 8 }}
          >
            <option value="">All models</option>
            {models.map((model) => (
              <option key={model} value={model}>{model}</option>
            ))}
          </select>
        </label>
        <TouchButton onClick={refetch}>Refresh</TouchButton>
      </div>
      <div className="stat-row">
        <div className="stat">
          <div className="stat-label">PRs</div>
          <div className="stat-value">{data.summary.prs}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Merged</div>
          <div className="stat-value">{data.summary.merged}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Median CI rounds</div>
          <div className="stat-value">
            {data.summary.median_ci_rounds == null ? "—" : data.summary.median_ci_rounds}
          </div>
        </div>
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Weekly trends</h3>
        {data.weekly_trends.length === 0 ? (
          <EmptyState title="No weekly buckets yet" description="No pull requests recorded for this window." />
        ) : (
          <table className="staff-table" data-testid="delivery-weekly-trends">
            <thead>
              <tr>
                <th>Week</th>
                <th>PRs</th>
                <th>Merged</th>
                <th>Median CI rounds</th>
              </tr>
            </thead>
            <tbody>
              {data.weekly_trends.map((row) => (
                <tr key={row.week_start}>
                  <th scope="row">{row.week_start}</th>
                  <td>{row.prs}</td>
                  <td>{row.merged}</td>
                  <td>{row.median_ci_rounds == null ? "—" : row.median_ci_rounds}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Pull requests</h3>
        {rows.length === 0 ? (
          <EmptyState title="No pull requests" description="Clear the repo filter or wait for sync." />
        ) : (
          <table className="staff-table" data-testid="delivery-pr-list">
            <thead>
              <tr>
                <th>PR</th>
                <th>CI rounds</th>
                <th>Lead</th>
                <th>CI wait</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.repo_full}#${row.number}`}>
                  <th scope="row">
                    <a href={row.html_url} target="_blank" rel="noreferrer">
                      {row.repo_full.split("/").pop()}#{row.number}
                    </a>
                  </th>
                  <td>{row.ci_rounds_to_green ?? "—"}</td>
                  <td>{formatDuration(row.lead_time_seconds)}</td>
                  <td>{formatDuration(row.ci_wait_seconds)}</td>
                  <td>
                    <TouchButton onClick={() => void selectPull(row.repo_full, row.number)}>Checks</TouchButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {(pullDetail || pullLoading || pullError) && (
        <div className="card" data-testid="delivery-pull-detail">
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>
              {pullDetail ? pullDetail.pull.title : "Pull detail"}
            </h3>
            <TouchButton onClick={clearPull}>Close</TouchButton>
          </div>
          {pullLoading ? <p role="status">Loading check runs…</p> : null}
          {pullError ? <EmptyState variant="error" title="Pull detail failed" description={pullError} /> : null}
          {pullDetail ? (
            <ul>
              {pullDetail.check_runs.map((run) => (
                <li key={run.name}>
                  <a href={run.details_url} target="_blank" rel="noreferrer">{run.name}</a>
                  {" — "}
                  {run.conclusion ?? run.status}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </div>
  );
}
